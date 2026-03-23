#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════╗
# ║  ✦  GIT WIZARD  v3  —  fast by default, full by choice  ║
# ╚══════════════════════════════════════════════════════════╝
#
#  DEFAULT:   ./git-wizard.sh
#             status → stage all → commit → pull (rebase) → push → offer PR
#             Total keypresses for happy path: ~4 + typing commit message
#
#  ADVANCED:  ./git-wizard.sh m  (or press [m] after any flow)
#             Full menu: branches, log, restore, amend, tags, etc.
#
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────
R="\033[0m"; BOLD="\033[1m"; DIM="\033[2m"
RED="\033[31m"; GREEN="\033[32m"; YELLOW="\033[33m"
BLUE="\033[34m"; MAGENTA="\033[35m"; CYAN="\033[36m"

ok()   { echo -e "  ${GREEN}✔${R}  $1"; }
err()  { echo -e "  ${RED}✘${R}  $1"; }
info() { echo -e "  ${CYAN}•${R}  $1"; }
warn() { echo -e "  ${YELLOW}!${R}  $1"; }
step() { echo -e "  ${MAGENTA}▶${R}  $1"; }
div()  { echo -e "  ${DIM}────────────────────────────────────────${R}"; }

# ── Single-key read (no Enter needed) ─────────────────────────
getkey() { local k; IFS= read -r -s -n1 k 2>/dev/null; echo "$k"; }

# Y/n — single keypress, Enter or y/Y = yes, n/N = no
yn() {
  echo -ne "  ${BOLD}${CYAN}?${R}  $1 ${DIM}[Y/n]${R} "
  local k; k=$(getkey)
  echo "${k:--}"
  [[ "$k" =~ ^[nN]$ ]] && return 1 || return 0
}

# ── Git helpers ───────────────────────────────────────────────
check_repo()  { git rev-parse --is-inside-work-tree &>/dev/null || { err "Not a git repository."; exit 1; }; }
cur_branch()  { git symbolic-ref --short HEAD 2>/dev/null || git rev-parse --short HEAD; }
cur_remote()  { git remote 2>/dev/null | head -1; }
has_remote()  { [[ -n "$(cur_remote)" ]]; }
is_clean()    { [[ -z "$(git status --porcelain)" ]]; }

# ─────────────────────────────────────────────────────────────
#  INLINE STATUS  (shown at the top of every flow)
# ─────────────────────────────────────────────────────────────
show_status() {
  local b r
  b=$(cur_branch); r=$(cur_remote)
  echo ""
  echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${R}"
  echo -e "  ${BOLD}${CYAN}✦ GIT WIZARD${R}   branch: ${GREEN}${BOLD}${b}${R}   remote: ${DIM}${r:-none}${R}"
  echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${R}"
  echo ""

  if is_clean; then
    ok "Working tree is clean"
    echo ""; return 0
  fi

  git status --short | while IFS= read -r line; do
    local xy="${line:0:2}" fname="${line:3}"
    local color="$DIM"
    [[ "${xy:0:1}" != " " && "${xy:0:1}" != "?" ]] && color="$GREEN"
    [[ "${xy:1:1}" != " " ]] && color="$YELLOW"
    [[ "$xy" == "??" ]] && color="$RED"
    echo -e "  ${color}${xy}${R}  ${fname}"
  done

  echo ""
  local ns nu nt
  ns=$(git diff --cached --name-only | wc -l | tr -d ' ')
  nu=$(git diff --name-only | wc -l | tr -d ' ')
  nt=$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')
  local summary=""
  [[ $ns -gt 0 ]] && summary+="${GREEN}${ns} staged${R}  "
  [[ $nu -gt 0 ]] && summary+="${YELLOW}${nu} modified${R}  "
  [[ $nt -gt 0 ]] && summary+="${RED}${nt} untracked${R}"
  echo -e "  ${summary}"
  echo ""
}

# ─────────────────────────────────────────────────────────────
#  COMMIT MESSAGE
#  Free-form by default (like script 2).
#  Optional single-key conventional type prefix.
# ─────────────────────────────────────────────────────────────
get_commit_msg() {
  echo -e "  ${DIM}Optional conventional prefix — press key, or Enter to skip:${R}"
  echo -e "  ${CYAN}[1]${R}feat  ${CYAN}[2]${R}fix  ${CYAN}[3]${R}docs  ${CYAN}[4]${R}refactor  ${CYAN}[5]${R}test  ${CYAN}[6]${R}chore  ${CYAN}[7]${R}style  ${CYAN}[8]${R}perf"
  div
  echo -ne "  ${BOLD}${CYAN}?${R}  ${BOLD}Type key or Enter to skip${R} "
  local tk; tk=$(getkey); echo "$tk"

  local prefix=""
  case "$tk" in
    1) prefix="feat: "     ;;
    2) prefix="fix: "      ;;
    3) prefix="docs: "     ;;
    4) prefix="refactor: " ;;
    5) prefix="test: "     ;;
    6) prefix="chore: "    ;;
    7) prefix="style: "    ;;
    8) prefix="perf: "     ;;
  esac

  echo ""
  echo -ne "  ${BOLD}${CYAN}?${R}  ${BOLD}Commit message${R} ${DIM}(Enter = \"update\")${R} › ${prefix}"
  local subject; IFS= read -r subject
  [[ -z "$subject" ]] && subject="update"

  COMMIT_MSG="${prefix}${subject}"
}

# ─────────────────────────────────────────────────────────────
#  QUICK FLOW  ← the default path
#  stage → commit → pull (rebase) → push → offer PR
# ─────────────────────────────────────────────────────────────
quick_flow() {
  check_repo
  show_status

  if is_clean; then
    info "Nothing to commit."
    echo ""
    echo -ne "  ${DIM}Press [m] for the full menu, any other key to exit › ${R}"
    local k; k=$(getkey); echo ""
    [[ "$k" == "m" || "$k" == "M" ]] && advanced_menu
    return 0
  fi

  # ── Stage ────────────────────────────────────────────────
  if git diff --cached --quiet; then
    yn "Stage ALL changes?" \
      && { git add -A; ok "All changes staged"; } \
      || { info "Stage manually then re-run."; exit 0; }
  else
    ok "Using already-staged files"
  fi
  echo ""

  # ── Commit message ───────────────────────────────────────
  get_commit_msg
  echo ""
  echo -e "  ${BOLD}${MAGENTA}Message:${R} ${CYAN}\"${COMMIT_MSG}\"${R}"
  echo ""

  yn "Commit?" \
    && { git commit -m "$COMMIT_MSG"; echo ""; ok "Committed → $(git log --oneline -1)"; } \
    || { warn "Cancelled."; return 1; }
  echo ""

  # ── Push ─────────────────────────────────────────────────
  if ! has_remote; then
    warn "No remote configured — skipping push."; return 0
  fi

  local r b; r=$(cur_remote); b=$(cur_branch)

  yn "Push to ${r}/${b}?" || { ok "Committed but not pushed."; return 0; }

  step "Pushing..."
  if git push "$r" "$b" 2>/dev/null; then
    ok "Pushed to ${r}/${b}"
  else
    warn "Push rejected — pulling with rebase and retrying..."
    git fetch "$r"
    if git rebase "${r}/${b}"; then
      ok "Rebased onto remote"
      git push "$r" "$b" && ok "Pushed!"
    else
      err "Rebase conflict! Resolve then: git rebase --continue && git push"
      return 1
    fi
  fi
  echo ""

  # ── PR offer (only on non-main branches) ─────────────────
  if [[ "$b" != "main" && "$b" != "master" ]]; then
    yn "Create a Pull Request?" && do_pr
  fi

  echo ""
  echo -ne "  ${DIM}Press [m] for the full menu, any other key to exit › ${R}"
  local k; k=$(getkey); echo ""
  [[ "$k" == "m" || "$k" == "M" ]] && advanced_menu
}

# ─────────────────────────────────────────────────────────────
#  PULL REQUEST
# ─────────────────────────────────────────────────────────────
do_pr() {
  echo ""
  local b r_url platform
  b=$(cur_branch)
  r_url=$(git remote get-url "$(cur_remote)" 2>/dev/null || echo "")
  platform="unknown"
  [[ "$r_url" == *"github"* ]] && platform="github"
  [[ "$r_url" == *"gitlab"* ]] && platform="gitlab"

  local default_title; default_title=$(git log -1 --pretty=format:"%s" 2>/dev/null || echo "$b")

  echo -ne "  ${BOLD}${CYAN}?${R}  ${BOLD}PR title${R} ${DIM}(Enter = \"${default_title}\")${R} › "
  local pr_title; IFS= read -r pr_title
  [[ -z "$pr_title" ]] && pr_title="$default_title"

  echo -ne "  ${BOLD}${CYAN}?${R}  ${BOLD}Base branch${R} ${DIM}(Enter = main)${R} › "
  local base; IFS= read -r base; base="${base:-main}"

  echo -ne "  ${BOLD}${CYAN}?${R}  ${BOLD}PR body${R} ${DIM}(Enter to skip)${R} › "
  local body; IFS= read -r body

  echo -ne "  ${BOLD}${CYAN}?${R}  ${BOLD}Draft?${R} ${DIM}[y/N]${R} "
  local dk; dk=$(getkey); echo "$dk"
  local draft_flag=""; [[ "$dk" =~ ^[yY]$ ]] && draft_flag="--draft"
  echo ""

  case "$platform" in
    github)
      if command -v gh &>/dev/null; then
        step "Creating GitHub PR..."
        gh pr create --title "$pr_title" --body "${body:-}" --base "$base" $draft_flag \
          && ok "Pull Request created!"
      else
        warn "gh CLI not installed — opening browser..."
        _pr_browser_url "$r_url" "$b"
      fi ;;
    gitlab)
      if command -v glab &>/dev/null; then
        step "Creating GitLab MR..."
        glab mr create --title "$pr_title" --fill && ok "Merge Request created!"
      else
        warn "glab CLI not installed — opening browser..."
        _pr_browser_url "$r_url" "$b"
      fi ;;
    *)
      warn "Platform not detected — opening browser..."
      _pr_browser_url "$r_url" "$b" ;;
  esac
}

_pr_browser_url() {
  local url; url=$(echo "$1" | sed 's/git@github.com:/https:\/\/github.com\//; s/\.git$//')
  local link="${url}/compare/$2?expand=1"
  info "Open to create PR:"; echo -e "\n  ${CYAN}${BOLD}${link}${R}\n"
  command -v xdg-open &>/dev/null && xdg-open "$link" &>/dev/null & true
  command -v open     &>/dev/null && open     "$link" &>/dev/null & true
}

# ─────────────────────────────────────────────────────────────
#  FULL ADVANCED MENU
# ─────────────────────────────────────────────────────────────
advanced_menu() {
  while true; do
    clear
    check_repo
    show_status
    local b; b=$(cur_branch)

    echo -e "  ${CYAN}[q]${R} Quick commit+push     ${CYAN}[s]${R} Full status + recent log"
    echo -e "  ${CYAN}[p]${R} Pull                  ${CYAN}[u]${R} Push (with options)"
    echo -e "  ${CYAN}[r]${R} Pull Request          ${CYAN}[x]${R} Restore / stash"
    echo -e "  ${CYAN}[b]${R} Branches              ${CYAN}[l]${R} Log / history"
    echo -e "  ${CYAN}[a]${R} Amend last commit     ${CYAN}[t]${R} Tags"
    echo -e "  ${CYAN}[i]${R} Interactive stage     ${DIM}[0 or Esc] Exit${R}"
    div
    echo -ne "  ${BOLD}${CYAN}?${R}  ${BOLD}Key › ${R}"
    local c; c=$(getkey); echo "$c"; echo ""

    case "$c" in
      q|Q) quick_flow; return ;;
      s|S) detailed_status ;;
      p|P) do_pull ;;
      u|U) do_push_menu ;;
      r|R) do_pr ;;
      x|X) do_restore ;;
      b|B) do_branches ;;
      l|L) do_log ;;
      a|A) do_amend ;;
      t|T) do_tags ;;
      i|I) do_interactive_stage ;;
      $'\x1b'|0) echo -e "\n  ${DIM}Goodbye! ✦${R}\n"; exit 0 ;;
      *) warn "Unknown key: ${c}" ;;
    esac

    echo ""; echo -ne "  ${DIM}Any key to return to menu...${R}"; getkey >/dev/null
  done
}

# ─────────────────────────────────────────────────────────────
#  DETAILED STATUS
# ─────────────────────────────────────────────────────────────
detailed_status() {
  local r; r=$(cur_remote)
  echo -e "\n  ${BOLD}${BLUE}── Full Status ──${R}\n"

  if has_remote; then
    local tracking; tracking=$(git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>/dev/null) || tracking=""
    if [[ -n "$tracking" ]]; then
      local ah bh
      ah=$(git rev-list "$tracking"..HEAD --count 2>/dev/null || echo 0)
      bh=$(git rev-list HEAD.."$tracking" --count 2>/dev/null || echo 0)
      echo -e "  ${CYAN}⇅${R}  ${ah} commits ahead, ${bh} behind — ${DIM}${r}${R}"
    fi
  fi

  echo -e "\n  ${BOLD}Recent commits:${R}"
  git log --pretty=format:"  %C(yellow)%h%Creset %C(cyan)%ad%Creset  %s  %C(dim)%an%Creset" \
    --date=short -7 2>/dev/null
  echo -e "\n"
}

# ─────────────────────────────────────────────────────────────
#  PULL
# ─────────────────────────────────────────────────────────────
do_pull() {
  has_remote || { err "No remote configured."; return 1; }
  local r b; r=$(cur_remote); b=$(cur_branch)

  echo -e "\n  ${BOLD}${BLUE}── Pull ──${R}"
  echo -e "  ${CYAN}[1]${R} Rebase ${DIM}(default)${R}   ${CYAN}[2]${R} Merge   ${CYAN}[3]${R} Fast-forward only"
  div
  echo -ne "  ${BOLD}${CYAN}?${R}  ${BOLD}Strategy (Enter=rebase)${R} "
  local s; s=$(getkey); echo "${s:--}"; echo ""

  step "Fetching ${r}..."
  git fetch "$r"

  case "${s}" in
    2) git merge "${r}/${b}" && ok "Merged" ;;
    3) git merge --ff-only "${r}/${b}" && ok "Fast-forwarded" ;;
    *)
      if git rebase "${r}/${b}"; then ok "Rebased onto ${r}/${b}"
      else err "Conflict! Resolve then: git rebase --continue"; fi ;;
  esac
}

# ─────────────────────────────────────────────────────────────
#  PUSH (with options)
# ─────────────────────────────────────────────────────────────
do_push_menu() {
  has_remote || { err "No remote configured."; return 1; }
  local r b; r=$(cur_remote); b=$(cur_branch)

  echo -e "\n  ${BOLD}${BLUE}── Push ──${R}  ${DIM}${b} → ${r}${R}"
  echo -e "  ${CYAN}[1]${R} Normal  ${CYAN}[2]${R} Force-with-lease  ${CYAN}[3]${R} Set upstream  ${CYAN}[4]${R} Push all branches  ${CYAN}[5]${R} Push tags"
  div
  echo -ne "  ${BOLD}${CYAN}?${R}  ${BOLD}Option (Enter=normal)${R} "
  local o; o=$(getkey); echo "${o:--}"; echo ""

  case "${o}" in
    2) step "Force-push (safe)..."; git push --force-with-lease "$r" "$b" && ok "Force-pushed with lease" ;;
    3) step "Setting upstream..."; git push -u "$r" "$b" && ok "Upstream set to ${r}/${b}" ;;
    4) step "Pushing all..."; git push "$r" --all && ok "All branches pushed" ;;
    5) step "Pushing tags..."; git push "$r" --tags && ok "Tags pushed" ;;
    *)
      step "Pushing..."
      if git push "$r" "$b"; then ok "Pushed ${b} → ${r}"
      else
        warn "Rejected — try pulling first"
        yn "Pull (rebase) then push?" && {
          git fetch "$r"; git rebase "${r}/${b}" && git push "$r" "$b" && ok "Done"
        }
      fi ;;
  esac
}

# ─────────────────────────────────────────────────────────────
#  RESTORE / STASH
# ─────────────────────────────────────────────────────────────
do_restore() {
  echo -e "\n  ${BOLD}${BLUE}── Restore / Stash ──${R}"
  echo -e "  ${CYAN}[1]${R} Discard changes in file     ${CYAN}[2]${R} Unstage a file"
  echo -e "  ${CYAN}[3]${R} Restore from commit         ${CYAN}[4]${R} Discard ALL unstaged"
  echo -e "  ${CYAN}[5]${R} Undo last commit (staged)   ${CYAN}[6]${R} Undo last commit (hard)"
  echo -e "  ${CYAN}[7]${R} Stash                       ${CYAN}[8]${R} Pop stash   ${CYAN}[9]${R} List stashes"
  div
  echo -ne "  ${BOLD}${CYAN}?${R}  Key › "; local o; o=$(getkey); echo "$o"; echo ""

  case "$o" in
    1) echo -ne "  File › "; read -r f; git restore -- "$f" && ok "Restored: $f" ;;
    2) echo -ne "  File › "; read -r f; git restore --staged -- "$f" && ok "Unstaged: $f" ;;
    3)
      echo -ne "  Commit (e.g. HEAD~1) › "; read -r c
      echo -ne "  File › "; read -r f
      git restore --source="$c" -- "$f" && ok "Restored $f from $c" ;;
    4) yn "Discard ALL unstaged?" && git restore . && ok "Done" ;;
    5) yn "Undo last commit (keep staged)?" && git reset --soft HEAD~1 && ok "Undone — changes staged" ;;
    6) warn "PERMANENT!"; yn "Sure?" && git reset --hard HEAD~1 && ok "Deleted" ;;
    7)
      echo -ne "  Stash message (Enter to skip) › "; read -r m
      [[ -n "$m" ]] && git stash push -m "$m" || git stash push; ok "Stashed" ;;
    8)
      if git stash list | grep -q .; then
        git stash list
        echo -ne "  Index (Enter=0) › "; read -r idx
        git stash pop "stash@{${idx:-0}}" && ok "Stash applied"
      else warn "No stashes"; fi ;;
    9) git stash list | grep -q . \
         && git stash list | while IFS= read -r l; do echo -e "  ${DIM}${l}${R}"; done \
         || warn "No stashes" ;;
  esac
}

# ─────────────────────────────────────────────────────────────
#  BRANCHES
# ─────────────────────────────────────────────────────────────
do_branches() {
  local cur; cur=$(cur_branch)
  echo -e "\n  ${BOLD}${BLUE}── Branches ──${R}  ${DIM}on: ${GREEN}${cur}${R}"
  echo -e "  ${CYAN}[1]${R} List         ${CYAN}[2]${R} New          ${CYAN}[3]${R} Switch       ${CYAN}[4]${R} Create+Switch"
  echo -e "  ${CYAN}[5]${R} Rename       ${CYAN}[6]${R} Delete       ${CYAN}[7]${R} Merge here   ${CYAN}[8]${R} Rebase onto"
  div
  echo -ne "  ${BOLD}${CYAN}?${R}  Key › "; local o; o=$(getkey); echo "$o"; echo ""

  case "$o" in
    1)
      echo -e "  ${BOLD}Local:${R}"
      git branch -v | while IFS= read -r l; do echo -e "  ${CYAN}${l}${R}"; done
      echo -e "\n  ${BOLD}Remote:${R}"
      git branch -rv 2>/dev/null | while IFS= read -r l; do echo -e "  ${DIM}${l}${R}"; done ;;
    2) echo -ne "  New branch name › "; read -r n; git branch "$n" && ok "Created: $n" ;;
    3)
      git branch | sed 's/*//' | awk '{print "  "$1}'
      echo -ne "  Switch to › "; read -r n; git switch "$n" && ok "Switched: $n" ;;
    4) echo -ne "  New branch › "; read -r n; git switch -c "$n" && ok "Created + switched: $n" ;;
    5) echo -ne "  New name for '${cur}' › "; read -r n; git branch -m "$n" && ok "Renamed to: $n" ;;
    6)
      git branch | sed 's/*//' | awk '{print "  "$1}'
      echo -ne "  Delete › "; read -r n
      yn "Delete ${n}?" && git branch -d "$n" && ok "Deleted: $n" ;;
    7)
      git branch | sed 's/*//' | awk '{print "  "$1}'
      echo -ne "  Merge into '${cur}' › "; read -r n
      git merge "$n" && ok "Merged $n → $cur" ;;
    8)
      git branch | sed 's/*//' | awk '{print "  "$1}'
      echo -ne "  Rebase onto › "; read -r n
      git rebase "$n" && ok "Rebased $cur onto $n" ;;
  esac
}

# ─────────────────────────────────────────────────────────────
#  LOG
# ─────────────────────────────────────────────────────────────
do_log() {
  echo -e "\n  ${BOLD}${BLUE}── Log ──${R}"
  echo -e "  ${CYAN}[1]${R} Pretty (20)   ${CYAN}[2]${R} Oneline   ${CYAN}[3]${R} Graph   ${CYAN}[4]${R} Search   ${CYAN}[5]${R} Show commit   ${CYAN}[6]${R} Diff range"
  div
  echo -ne "  ${BOLD}${CYAN}?${R}  Key › "; local o; o=$(getkey); echo "$o"; echo ""

  case "$o" in
    1) git log --pretty=format:"  %C(yellow)%h%Creset %C(cyan)%ad%Creset %C(green)%an%Creset  %s%C(red)%d%Creset" --date=short -20 ;;
    2) git log --oneline -30 | while IFS= read -r l; do echo -e "  ${DIM}${l}${R}"; done ;;
    3) git log --oneline --graph --decorate --all -30 | while IFS= read -r l; do echo -e "  ${CYAN}${l}${R}"; done ;;
    4) echo -ne "  Search › "; read -r t; git log --all --oneline --grep="$t" ;;
    5) echo -ne "  Commit hash › "; read -r h; git show "$h" ;;
    6)
      echo -ne "  From (e.g. HEAD~5) › "; read -r f
      echo -ne "  To   (e.g. HEAD)   › "; read -r t
      git diff "${f}..${t}" --stat ;;
    *) git log --oneline --graph --decorate --all -20 | while IFS= read -r l; do echo -e "  ${CYAN}${l}${R}"; done ;;
  esac
}

# ─────────────────────────────────────────────────────────────
#  AMEND
# ─────────────────────────────────────────────────────────────
do_amend() {
  local last; last=$(git log -1 --pretty=format:"%s")
  echo -e "\n  ${BOLD}${BLUE}── Amend ──${R}  ${DIM}last: ${CYAN}${last}${R}"
  echo -e "  ${CYAN}[1]${R} New message only   ${CYAN}[2]${R} Add staged (keep msg)   ${CYAN}[3]${R} Both"
  div
  echo -ne "  ${BOLD}${CYAN}?${R}  Key › "; local o; o=$(getkey); echo "$o"; echo ""

  case "$o" in
    2) git commit --amend --no-edit && ok "Staged changes added to last commit" ;;
    *)
      echo -ne "  New message (Enter to keep current) › "
      local m; IFS= read -r m
      [[ -z "$m" ]] && m="$last"
      git commit --amend -m "$m" && ok "Amended" ;;
  esac
  warn "If already pushed: git push --force-with-lease"
}

# ─────────────────────────────────────────────────────────────
#  TAGS
# ─────────────────────────────────────────────────────────────
do_tags() {
  echo -e "\n  ${BOLD}${BLUE}── Tags ──${R}"
  echo -e "  ${CYAN}[1]${R} List   ${CYAN}[2]${R} Lightweight   ${CYAN}[3]${R} Annotated   ${CYAN}[4]${R} Push   ${CYAN}[5]${R} Delete"
  div
  echo -ne "  ${BOLD}${CYAN}?${R}  Key › "; local o; o=$(getkey); echo "$o"; echo ""
  local r; r=$(cur_remote)

  case "$o" in
    1) git tag -l -n | while IFS= read -r l; do echo -e "  ${CYAN}${l}${R}"; done ;;
    2) echo -ne "  Tag name › "; read -r t; git tag "$t" && ok "Tagged: $t" ;;
    3)
      echo -ne "  Tag name › "; read -r t
      echo -ne "  Message  › "; read -r m
      git tag -a "$t" -m "$m" && ok "Annotated tag: $t" ;;
    4) has_remote && git push "$r" --tags && ok "All tags pushed to $r" || err "No remote" ;;
    5)
      git tag -l | nl
      echo -ne "  Tag to delete › "; read -r t
      yn "Delete $t locally?" && git tag -d "$t" && ok "Local tag deleted"
      has_remote && yn "Delete remote tag too?" && git push "$r" --delete "$t" && ok "Remote tag deleted" ;;
  esac
}

# ─────────────────────────────────────────────────────────────
#  INTERACTIVE STAGE
# ─────────────────────────────────────────────────────────────
do_interactive_stage() {
  echo -e "\n  ${BOLD}${BLUE}── Stage Files ──${R}"
  local -a files; mapfile -t files < <(git status --short | awk '{print $2}')
  if [[ ${#files[@]} -eq 0 ]]; then warn "Nothing to stage."; return; fi

  for i in "${!files[@]}"; do
    local s; s=$(git status --short -- "${files[$i]}" | head -1 | cut -c1-2)
    echo -e "  ${CYAN}[$((i+1))]${R}  ${DIM}${s}${R}  ${files[$i]}"
  done
  echo -e "\n  ${DIM}Numbers (e.g. 1 3), or [a]=all  [p]=patch  [q]=skip${R}"
  div
  echo -ne "  ${BOLD}${CYAN}?${R}  Choice › "
  local sel; IFS= read -r sel

  case "$sel" in
    a|A) git add -A && ok "All staged" ;;
    p|P) git add -p && ok "Patch done" ;;
    q|Q) info "Skipped" ;;
    *)
      for n in $sel; do
        local idx=$((n-1))
        [[ $idx -ge 0 && $idx -lt ${#files[@]} ]] \
          && git add -- "${files[$idx]}" && ok "Staged: ${files[$idx]}" \
          || warn "Invalid: $n"
      done ;;
  esac
}

# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
check_repo

case "${1:-}" in
  ""|quick|q)   quick_flow ;;
  menu|m)       advanced_menu ;;
  status|s)     show_status; detailed_status ;;
  pull)         do_pull ;;
  push)         do_push_menu ;;
  pr)           do_pr ;;
  restore|r)    do_restore ;;
  branch|b)     do_branches ;;
  log|l)        do_log ;;
  amend|a)      do_amend ;;
  tags|t)       do_tags ;;
  stage)        do_interactive_stage ;;
  -h|--help|help)
    echo ""
    echo -e "${BOLD}${CYAN}Git Wizard v3${R}  Usage: ${BOLD}./git-wizard.sh [cmd]${R}\n"
    echo -e "  ${DIM}(no args)${R}  Quick: stage → commit → pull → push → PR  ${DIM}← default${R}"
    echo -e "  ${CYAN}m${R}          Full menu (branches, log, restore, tags…)"
    echo -e "  ${CYAN}s${R}          Status"
    echo -e "  ${CYAN}pull${R}       Pull"
    echo -e "  ${CYAN}push${R}       Push"
    echo -e "  ${CYAN}pr${R}         Pull Request"
    echo -e "  ${CYAN}r${R}          Restore / stash"
    echo -e "  ${CYAN}b${R}          Branches"
    echo -e "  ${CYAN}l${R}          Log"
    echo -e "  ${CYAN}a${R}          Amend"
    echo -e "  ${CYAN}t${R}          Tags"
    echo -e "  ${CYAN}stage${R}      Interactive stage"
    echo ""
    ;;
  *) err "Unknown command: ${1}"; echo -e "  Run ${CYAN}./git-wizard.sh help${R}"; exit 1 ;;
esac