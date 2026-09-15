# Generated project bundles

Where the generator writes project bundles when run with `--out corpus` locally. These outputs are
**not committed** — the corpus is generated on demand, and for the full corpus the bundles are hosted
as versioned datasets (SPEC §11.7). Generate them with:

```
uv run --project corpus python -m corpus.generator --out <dir>
```

The committed generator lives in [`../generator/`](../generator/).
