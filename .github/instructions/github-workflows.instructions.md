---
description: 'GitHub Actions workflow conventions for this repo'
applyTo: '.github/workflows/*.yml,.github/workflows/*.yaml,.github/actions/*/*.yml'
---
# GitHub Actions workflow conventions

Applies to CI workflows and composite actions. YAML formatting rules come from
[yaml.instructions.md](./yaml.instructions.md); this file holds the Actions-specific
review rules. Flag a violation of any of them.

## Naming
- Every job has a `name` that summarises what it does.
- Every step has a `name`. Add an `id` only when a later step references its
  outputs (keep ids short, lowercase, hyphenated).

## No `${{ }}` inside `run:` scripts
GitHub interpolates `${{ }}` into the script before the shell runs, which is a
shell-injection risk and makes the step impossible to run or debug outside Actions.
Bind the expression to an `env:` entry on the step and read `$VAR` in bash. The step
then works locally with the same env set. This is the first rule to check.

```yaml
# Bad: the expression is in the script
- name: Tag image
  run: docker tag app:latest app:${{ github.sha }}

# Good: the expression is on env:, the script reads $VAR
- name: Tag image
  env:
    SHA: ${{ github.sha }}
  run: docker tag app:latest "app:$SHA"
```
Prefer `env:` for static or derivable values too.

## Comment above the step
Put explanatory `#` comments as YAML comments above the step, so the `run:` body
stays a plain script.

```yaml
# Wait for the DB to accept connections before migrating: the container reports
# ready before the socket is up.
- name: Wait for database
  run: |
    until pg_isready -h localhost; do sleep 1; done
```

## One job per step
If a step does several things (build, sign, upload), split it into separate `run:`
steps, each with one tool and clear inputs and outputs. A failure then points at the
step that broke. Pass data between them through step outputs (below).

## `GITHUB_ENV` only for flow-wide values; otherwise use step outputs
- Write to `GITHUB_ENV` only when many later steps need the value: every step
  after it sees it.
- When data passes from one step to one other, use a step output
  (`echo "key=value" >> "$GITHUB_OUTPUT"`, read as `${{ steps.<id>.outputs.key }}`)
  or a job output.

## Look up action and tool versions
Before adding or bumping any `uses:` pin, confirm the current version, for example
with `gh api repos/<owner>/<repo>/releases/latest --jq .tag_name`. A remembered
`@vN` is often a stale major.

## Other conventions
- Provision first, act second: the install and setup steps up front, then a
  readiness gate, then the work. Don't interleave installing and using.
- Invoke scripts as `bash path/to/x.sh`, not through the executable bit.
- For multiline output, use a `cat` heredoc, not many `echo`s:
  ```yaml
  - name: Print summary
    run: |
      cat <<'EOF'
      line one
      line two
      EOF
  ```
- Never `echo` a secret, and `::add-mask::` a value derived from one. No secret goes
  in a step name or an output.
