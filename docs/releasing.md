# Releasing

## One-time PyPI setup

Create the `parentsquare-client` project on PyPI and TestPyPI, then add a
Trusted Publisher for each project with:

- owner: `joebeeson`
- repository: `parentsquare-client`
- workflow: `.github/workflows/publish.yml`
- environment: `pypi`

The workflow uses GitHub's OIDC token, so no PyPI API token belongs in GitHub
secrets. Configure the `pypi` GitHub environment with an approval rule if
published releases should wait for a final review.

The first publication is normally a TestPyPI rehearsal. From the repository's
`Actions` tab, run `Publish to Python Package Index` with `repository` set to
`testpypi`. Install that exact candidate with:

```bash
python -m pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ parentsquare-client
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ parentsquare-client
```

TestPyPI and PyPI are separate indexes. Use a new version for every rehearsal
and final publication.

## Release flow

1. Update the version in `pyproject.toml` and run `uv lock`.
2. Run `uv run --extra dev pytest`, `ruff`, and `prek` locally.
3. Commit the version change and push a tag such as `v0.1.2`.
4. The tag workflow runs tests, builds the wheel and source archive, and creates
   the GitHub release with generated notes.
5. Publishing the GitHub release triggers the Trusted Publishing workflow.

Use the manual `Publish to Python Package Index` workflow with the `testpypi`
target for a release candidate such as `0.1.2rc1`.
