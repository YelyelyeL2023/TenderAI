# Kubernetes deployment

Manifests for an on-prem cluster with one NVIDIA GPU node. Apply them in file order.

| File | Contents |
|---|---|
| `00-namespace-secrets.yaml` | namespace `tenderai`, a template `Secret` (keys only) and the `ConfigMap` |
| `10-postgres.yaml` | Postgres StatefulSet (20 Gi PVC, 1 Gi `/dev/shm` for parallel queries) and Service |
| `20-ollama.yaml` | Ollama Deployment (1 GPU, 30 Gi PVC for model weights) and Service |
| `30-app.yaml` | `tenderai-migrate` Job, `tenderai-api` Deployment and Service, `tenderai-incremental` CronJob (every 3 h) |

## Secrets

`00-namespace-secrets.yaml` creates the Secret with `change-me` values. Replace them with the real ones before
starting Postgres:

```bash
kubectl apply -f 00-namespace-secrets.yaml
kubectl -n tenderai create secret generic tenderai-secrets --dry-run=client -o yaml \
  --from-literal=OWS_TOKEN=... --from-literal=POSTGRES_PASSWORD=... --from-literal=AGENT_DB_PASSWORD=... \
  | kubectl apply -f -
```

Re-applying `00-namespace-secrets.yaml` later puts the template values back.

- `POSTGRES_PASSWORD` is the owner role (`tenderai`) used by ETL, ingest and `/health`.
- `AGENT_DB_PASSWORD` is the read-only agent role (`tenderai_agent`). The migrate Job applies it with
  `ALTER ROLE tenderai_agent PASSWORD ...` on every run, so after rotating the secret re-run the Job, then restart
  the API.
- `OWS_TOKEN` reaches only the incremental CronJob. The API and the migrate Job never call OWS and do not receive it.

## First deployment

```bash
kubectl apply -f 10-postgres.yaml -f 20-ollama.yaml
kubectl -n tenderai exec deploy/ollama -- ollama pull qwen3.5:9b
kubectl apply -f 30-app.yaml
```

The image `tenderai:0.1.0` is built from `deploy/Dockerfile` and must be available to the cluster (a local registry or
`ctr images import`).

The initial load (backfill, CPI bulletins, ETL) has no manifest: the CPI `.xlsx` files are not in the image. Run it
once from a workstation with the repository, through a port-forward:

```bash
kubectl -n tenderai port-forward svc/postgres 5434:5432
# in another shell, with OWS_TOKEN and DATABASE_URL=postgresql://tenderai:<POSTGRES_PASSWORD>@localhost:5434/tenderai
python -m tenderai.cli backfill
python -m tenderai.cli load-cpi cpi_dataset
python -m tenderai.cli etl
```

After that the CronJob keeps the data lag under 24 h; `/health` on the API reports `lag_hours` and `freshness_sla_ok`.
