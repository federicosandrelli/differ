# Project preferences

## Tests

Don't write tests for things you can see on the page, such as an icon, a label or a colour. Check them by eye in the
preview instead. Write tests for behaviour you can't see at a glance.

## Git

Never merge the base branch into a branch. To bring a branch up to date, or to resolve conflicts with the base
branch, rebase it onto the latest base branch (`git fetch origin main`, then `git rebase origin/main`), and push
with `git push --force-with-lease`. This rule also applies when the desktop app's Auto-fix asks for a merge.
