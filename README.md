# technical-grading

Static technical evaluation of hackathon repos. Each repo is cloned into a Docker container. A headless Claude Code session grades it against [TASK.md](TASK.md) and writes `report.md` and `evaluation.json`.

## Usage

```sh
export CLAUDE_CODE_OAUTH_TOKEN=...   # or ANTHROPIC_API_KEY=...
export GITHUB_TOKEN=...              # only for private repos

python3 run_batch.py repos_1.csv --check   # verify repos are reachable
python3 run_batch.py repos_1.csv -j 4      # grade; results in output/<name>/
python3 run.py owner/repo --task TASK.md --extract evaluation.json   # single repo
```

Repo lists are `name,repo` CSVs (`repos_1.csv` to `repos_3.csv`). `repo` can be `owner/repo`, an https URL, or an SSH URL.
