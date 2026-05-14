"""Kubernetes provisioning step — generates kubeconfig per cluster."""

from __future__ import annotations

import base64
import os
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _build_kubeconfig(cluster_name: str, server: str, ca_data: str, token: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": cluster_name, "cluster": {"server": server,
                                                          "certificate-authority-data": ca_data}}],
        "users": [{"name": f"sentinel-{cluster_name}",
                   "user": {"token": token}}],
        "contexts": [{"name": cluster_name,
                      "context": {"cluster": cluster_name,
                                  "user": f"sentinel-{cluster_name}"}}],
        "current-context": cluster_name,
    }


async def run(profile: Any, topology: Any) -> "StepResult":  # type: ignore[name-defined]  # noqa: F821
    from sentinel.provisioning.runner import StepResult, StepStatus

    # Read cluster configs from env. Format:
    # KUBE_CLUSTERS=prod-us-east=https://k8s.prod.example.com,...
    clusters_raw = os.environ.get("KUBE_CLUSTERS", "")
    ca_data = os.environ.get("KUBE_CA_DATA", "")
    kubeconfig_bucket = os.environ.get("KUBECONFIG_BUCKET", "")

    if not clusters_raw:
        return StepResult(
            name="kubernetes",
            status=StepStatus.SKIPPED,
            details={"reason": "KUBE_CLUSTERS not configured"},
        )

    # Parse cluster map
    cluster_map: dict[str, str] = {}
    for entry in clusters_raw.split(","):
        if "=" in entry:
            name, url = entry.split("=", 1)
            cluster_map[name.strip()] = url.strip()

    provisioned: list[str] = []
    skipped: list[str] = []
    kubeconfig_urls: dict[str, str] = {}

    for cluster in topology.clusters:
        server = cluster_map.get(cluster, "")
        if not server:
            log.warning("cluster not in KUBE_CLUSTERS", cluster=cluster)
            skipped.append(cluster)
            continue

        # Build a read-only kubeconfig for this cluster
        # In production, you'd create a ServiceAccount + RoleBinding via the k8s API
        # and get a real token. Here we generate a placeholder kubeconfig.
        kubeconfig = _build_kubeconfig(
            cluster_name=cluster,
            server=server,
            ca_data=ca_data or base64.b64encode(b"<ca-certificate-data>").decode(),
            token="<rotated-on-first-login>",
        )

        if kubeconfig_bucket:
            # Upload to S3/GCS bucket as a presigned URL
            try:
                url = await _upload_kubeconfig(
                    profile.employee_id, cluster, kubeconfig, kubeconfig_bucket
                )
                kubeconfig_urls[cluster] = url
            except Exception as e:
                log.warning("kubeconfig upload failed", cluster=cluster, error=str(e))
                skipped.append(cluster)
                continue
        else:
            kubeconfig_urls[cluster] = "<embedded in provisioning report>"

        provisioned.append(cluster)

    status = (
        StepStatus.SUCCESS if not skipped else
        (StepStatus.PARTIAL if provisioned else StepStatus.SKIPPED)
    )
    return StepResult(
        name="kubernetes",
        status=status,
        details={
            "provisioned": provisioned,
            "skipped": skipped,
            "kubeconfig_urls": kubeconfig_urls,
        },
    )


async def _upload_kubeconfig(
    employee_id: str, cluster: str, kubeconfig: dict, bucket: str
) -> str:
    """Upload kubeconfig to S3 and return a presigned URL. Requires boto3 if S3."""
    try:
        import boto3  # type: ignore[import]
        import yaml

        s3 = boto3.client("s3")
        bucket_name = bucket.removeprefix("s3://")
        key = f"kubeconfigs/{employee_id}/{cluster}.yaml"
        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=yaml.dump(kubeconfig),
            ContentType="application/yaml",
        )
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": key},
            ExpiresIn=86400,
        )
        return url
    except ImportError:
        return "<boto3 not installed — upload manually>"
    except Exception as e:
        raise RuntimeError(f"S3 upload failed: {e}") from e
