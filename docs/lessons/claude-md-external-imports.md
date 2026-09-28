# Lesson: `@import` of a file outside the project needs approval; a symlink does not

You want personal rules for one repository (the `solo` rule, local paths, your worker ceilings) without committing them
to the repository. The obvious way is an import line in a file Claude Code reads:

```markdown
<!-- CLAUDE.local.md at the root of the checkout -->
@~/agent-rules/app.md
```

It works on some machines and silently does nothing on others.

## What happens

Claude Code only follows an import that points **outside the project** when that project has approved external
imports. The approval is given once, in an interactive session, through a dialog, and stored per project in
`~/.claude.json` (the key observed in Claude Code 2.1.x is `hasClaudeMdExternalIncludesApproved`). The project key is
the root of the main repository, so git worktrees of the same repository share the approval of the main checkout.

A project that was never approved does not load the import in any mode. In a headless run (`claude -p`), which never
shows the dialog, the import line reaches the model as plain text. The rules are simply not there, and nothing warns
you. It also breaks when the approval is lost: the project entry is deleted, or the repository moves to another folder
(for example out of a synced folder, see [the iCloud lesson](icloud-synced-folders.md)), which creates a new project
key without the approval.

## What works everywhere: a symlink

`CLAUDE.md` and `CLAUDE.local.md` themselves are always loaded, and Claude Code reads them through a symlink without
any approval. So point the file at your personal rules instead of importing them:

```bash
cd ~/code/app
ln -s ~/agent-rules/app.md CLAUDE.local.md
echo CLAUDE.local.md >> .git/info/exclude     # ignored locally, never committed
```

- Worktrees under `.claude/worktrees/` read the `CLAUDE.local.md` at the root of the main checkout (without duplicating
  the checkout's `CLAUDE.md`).
- Worktree managers that put worktrees under a parent folder (`~/workspaces/app/<worktree>`) load a `CLAUDE.md` from
  that parent folder through the directory hierarchy. Make that file a symlink to the same personal file, and each
  repository has one source of truth.

## Verify, do not assume

```bash
cd ~/code/app                      # and once inside a worktree
claude -p "Quote the rule you follow for running a full test suite on this machine."
```

If the answer does not quote your rule (or quotes the literal `@~/...` line), the file is not being loaded.
