# Releasing

## One-time PyPI setup

Create the `parentsquare-client` project on PyPI and TestPyPI, then add a
Trusted Publisher for each project with the matching environment:

- owner: `joebeeson`
- repository: `parentsquare-client`
- workflow: `.github/workflows/publish.yml`
- PyPI environment: `pypi`
- TestPyPI environment: `testpypi`

The workflow uses GitHub's OIDC token, so no PyPI API token belongs in GitHub
secrets. Configure the `testpypi` GitHub environment for candidate runs and
the `pypi` environment with required approval and final-tag restrictions.

Release candidates go to TestPyPI. From the repository's `Actions` tab, run
`Publish to Python Package Index` with `repository` set to `testpypi`. Install
that exact candidate with:

```bash
python -m pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ parentsquare-client
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ parentsquare-client
```

TestPyPI and PyPI are separate indexes. A version can be uploaded once to each
index, but cannot be overwritten on the same index.

## Release flow

1. Update the version in `pyproject.toml` and run `uv lock`.
2. Run `uv run --extra dev pytest`, `ruff`, and `prek` locally.
3. Commit the version change and push a tag such as `v0.1.2`.
4. The tag workflow runs tests, builds the wheel and source archive, and creates
   the GitHub release with generated notes.
5. For a release candidate such as `v0.1.2rc1`, manually dispatch the publish
   workflow with `repository=testpypi` and test the candidate.
6. For a final tag, manually dispatch the publish workflow with
   `repository=pypi` after the release has passed its final review. The
   production environment should only allow final version tags.
