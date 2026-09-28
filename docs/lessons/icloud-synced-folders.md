# Lesson: keep repositories out of synced folders

On macOS, "Desktop & Documents Folders" in iCloud Drive syncs everything under `~/Documents` and `~/Desktop`. With
"Optimize Mac Storage" on, files the system considers cold are evicted and kept only in the cloud (dataless): they
still show up in `ls`, but reading them triggers a download. A git repository with its dependencies in there behaves
badly in ways that look like test flakiness or memory trouble, and parallel agents make it worse.

## What we saw in a monorepo that lived under ~/Documents

- **Thousands of dataless files**, mostly in `node_modules`, `dist` and `.next`, plus a few inside `.git`. Any tool that
  walks those folders (a test runner, `tsc`, a bundler) stalls while they download one by one. An e2e run sat at 0% CPU
  before compiling anything.
- **Conflict copies named `name 2`, `name 3`...** created by the sync: thousands of them in build folders, some inside
  `node_modules`, and inside `.git` (`index 2` up to `index 11`, and duplicated refs such as `refs/heads/feature 2`).
  The duplicated refs broke `git fetch` until they were moved away.
- **git itself fights the sync.** A plain `git status` rewrites the index, which the sync picks up and uploads; reading
  history in a `.git` with evicted objects forces downloads. Use `git --no-optional-locks status`, `git ls-remote`, or
  clone again from the remote instead of inspecting the old copy.
- **The sync queue can stall for hours.** `brctl status` sometimes printed a partial output that looked like "queue
  drained" when it was not; it is not a reliable signal.
- **Secrets travel too.** `.env` files and credential JSONs under a synced folder go to the cloud. Without Advanced Data
  Protection they are not end-to-end encrypted: treat every secret that lived there as exposed and rotate it.

## Checking a machine

```bash
defaults read com.apple.bird optimize-storage        # 1 = Optimize Mac Storage is on
ls -lO ~/Documents/some-repo | grep dataless         # evicted files carry the "dataless" flag
find ~/Documents ~/Desktop -name .git -maxdepth 4    # repositories inside synced folders
```

## Moving a repository out

1. Close every session (agent, editor, terminal) whose working directory is the old path:
   `lsof -d cwd | grep /Users/you/Documents/app` must print nothing. A session left open there keeps writing to the
   old copy (hooks, memory files, caches).
2. Push everything. Uncommitted work goes to a backup branch on the remote first, and you come back to the original
   branch with the working tree intact.
3. Clone again from the remote into a folder that is not synced (for example `~/code/app`), instead of copying the old
   folder: a copy drags the conflict duplicates along and forces every dataless file to download.
4. Reinstall dependencies and rebuild in the new folder (`node_modules`, virtual environments, build output). A Python
   virtual environment does not survive a move: recreate it from a `pip freeze` of the old one.
5. Run the full validation once in the new folder (through `solo`) and compare with the old results.
6. Remove the secrets from the old copy first, then the copy itself. Rotate the secrets that lived there.
7. Update whatever points at the old path: worktree managers, editor workspaces, agent memory folders, scheduled jobs.

## Why this matters for parallel agents

Every agent worktree is another full set of files for the sync to track, and every suite run rewrites caches and build
output that the sync then uploads. A machine that is already short on memory also spends CPU and I/O on the sync
daemon. Moving the repositories out is one of the cheapest performance fixes available.
