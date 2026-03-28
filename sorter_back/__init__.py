"""
sorter_back/__init__.py — YouTube video sorter (Python port)
------------------------------------------------------------
Exposes:
  • VideoSorter  — class that holds a list of raw video objects and lets
                   you rank them by any numeric metric (ratio, likes, views).
  • sort_videos  — standalone convenience function; same logic, no class needed.

Data shape expected (mirrors what the JS api.js + popup.js produce):
    video = {
        "id":    {"videoId": "dQw4w9WgXcQ"},          # from YouTube Search API
        "snippet": {"title": "Never Gonna Give You Up"},
        # optional pre-fetched stats (if absent they default to 0)
        "stats": {
            "viewCount":  "1000000",
            "likeCount":  "50000",
        }
    }
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Iterable, List, Literal

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

Metric = Literal["ratio", "likes", "views"]

@dataclass(order=True)
class _RankedVideo:
    """Internal sortable wrapper. Sorted ascending so we can use bisect."""
    score: float
    title: str  = field(compare=False)
    video_id: str = field(compare=False)
    likes: int    = field(compare=False)
    views: int    = field(compare=False)
    ratio: float  = field(compare=False)

    @property
    def as_dict(self) -> dict:
        return {
            "title":    self.title,
            "video_id": self.video_id,
            "likes":    self.likes,
            "views":    self.views,
            "ratio":    self.ratio,
        }


def _parse_stats(video: dict) -> tuple[int, int, float]:
    """
    Extract (views, likes, ratio) from a video dict.
    The JS fetchVideoStats() function does exactly this calculation:

        ratio = likeCount / viewCount   (rounded to 3 dp in JS, kept as float here)

    Stats live under video["stats"] if pre-fetched; otherwise default to 0.
    """
    raw = video.get("stats", {})
    views = int(raw.get("viewCount", 0) or 0)
    likes = int(raw.get("likeCount", 0) or 0)
    ratio = round(likes / views, 6) if views > 0 else 0.0
    return views, likes, ratio


def _build_ranked(video: dict, metric: Metric) -> _RankedVideo:
    views, likes, ratio = _parse_stats(video)
    score_map = {"ratio": ratio, "likes": float(likes), "views": float(views)}
    return _RankedVideo(
        score    = score_map[metric],
        title    = video.get("snippet", {}).get("title", "(unknown)"),
        video_id = video.get("id", {}).get("videoId", ""),
        likes    = likes,
        views    = views,
        ratio    = ratio,
    )


def _top_n_insert(heap: List[_RankedVideo], ranked: _RankedVideo, top_n: int) -> None:
    """
    Mirrors the JS updateTopVideos() function exactly:

    JS logic (from popup.js):
        for (let i = 0; i < topVideos.length; i++) {
            if (metricValue > topVideos[i][metricKey]) {
                topVideos.splice(i, 0, {...});   // insert at position i
                topVideos.pop();                  // drop the last (lowest) entry
                break;
            }
        }

    The JS array is kept sorted *descending* (highest score first).
    Here we mirror that with a descending list and bisect for O(log n) insertion.
    """
    # heap is kept ascending internally; we negate for bisect to get descending order
    neg_score = -ranked.score
    keys = [-v.score for v in heap]
    pos = bisect.bisect_right(keys, neg_score)

    if pos < top_n:
        heap.insert(pos, ranked)
        if len(heap) > top_n:
            heap.pop()          # drop lowest-scoring tail (same as JS .pop())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class VideoSorter:
    """
    Holds a collection of raw YouTube video dicts and ranks them.

    Usage
    -----
    >>> sorter = VideoSorter(raw_videos)
    >>> top10_by_ratio = sorter.top(10, metric="ratio")
    >>> top5_by_likes  = sorter.top(5,  metric="likes")
    >>> all_by_views   = sorter.sorted_all(metric="views")   # descending
    """

    def __init__(self, videos: Iterable[dict]) -> None:
        self._videos: List[dict] = list(videos)

    # ------------------------------------------------------------------
    # Core ranking method — directly equivalent to updatePopup() in JS
    # ------------------------------------------------------------------

    def top(self, n: int = 10, *, metric: Metric = "ratio") -> List[dict]:
        """
        Return the top-N videos ranked by *metric* (descending).

        Parameters
        ----------
        n      : how many results to return (default 10, same as JS default).
        metric : "ratio"  — likes ÷ views  (default; what the extension shows)
                 "likes"  — raw like count
                 "views"  — raw view count

        Returns a list of plain dicts with keys:
            title, video_id, likes, views, ratio
        """
        if n <= 0:
            return []

        # Seed with n empty slots — mirrors JS:
        #   Array(10).fill({ ratio: 0, title: "", ... })
        heap: List[_RankedVideo] = [
            _RankedVideo(0.0, "", "", 0, 0, 0.0) for _ in range(n)
        ]

        for video in self._videos:
            ranked = _build_ranked(video, metric)
            _top_n_insert(heap, ranked, n)

        # Return only entries that were actually filled (score > 0)
        return [r.as_dict for r in heap if r.score > 0]

    def sorted_all(self, *, metric: Metric = "ratio") -> List[dict]:
        """Return ALL videos sorted by *metric* (descending, no cap)."""
        ranked = [_build_ranked(v, metric) for v in self._videos]
        ranked.sort(reverse=True)
        return [r.as_dict for r in ranked]

    def __len__(self) -> int:
        return len(self._videos)

    def __repr__(self) -> str:
        return f"VideoSorter({len(self._videos)} videos)"


# ---------------------------------------------------------------------------
# Standalone convenience function
# ---------------------------------------------------------------------------

def sort_videos(
    videos: Iterable[dict],
    *,
    metric: Metric = "ratio",
    top_n: int | None = None,
) -> List[dict]:
    """
    Sort *videos* by *metric* and return them in descending order.

    Parameters
    ----------
    videos  : iterable of raw video dicts (same shape as YouTube Search API items).
    metric  : "ratio" | "likes" | "views"  (default "ratio").
    top_n   : if given, return only the top N results (mirrors JS behaviour).
              if None (default), return all videos sorted.

    Examples
    --------
    >>> results = sort_videos(my_videos, metric="ratio", top_n=10)
    >>> results = sort_videos(my_videos, metric="likes")
    """
    sorter = VideoSorter(videos)
    if top_n is not None:
        return sorter.top(top_n, metric=metric)
    return sorter.sorted_all(metric=metric)
__all__ = ["VideoSorter","sort_videos"]