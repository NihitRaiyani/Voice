# Native observability lab

These files do not start, install or download anything. Use existing native Prometheus/Grafana binaries. Start the application with its documented native command and one API worker on port 8000; the registry is process-local. Multiple workers need individual scrape targets/registries before aggregation is meaningful. Worker-process telemetry is separate and is not scraped by this API target.

Run the commands from the repository root in separate terminals:

```sh
mkdir -p var/roma/observability/prometheus
promtool check config configs/observability/prometheus.yml
prometheus --config.file=configs/observability/prometheus.yml --web.listen-address=127.0.0.1:9090 --storage.tsdb.path=var/roma/observability/prometheus --storage.tsdb.retention.time=1d
```

Set `GF_SECURITY_ADMIN_PASSWORD` privately before the first Grafana start. Do not commit it. Grafana also requires its installed assets/homepath (for example the directory containing `conf/defaults.ini`); use the native installation's own path:

```sh
export ROMA_GRAFANA_HOME="/absolute/path/to/installed/grafana"
export ROMA_DASHBOARD_DIR="$PWD/configs/observability/dashboards"
export GF_PATHS_DATA="$PWD/var/roma/observability/grafana/data"
export GF_PATHS_LOGS="$PWD/var/roma/observability/grafana/logs"
export GF_PATHS_PLUGINS="$PWD/var/roma/observability/grafana/plugins"
export GF_PATHS_PROVISIONING="$PWD/configs/observability/provisioning"
export GF_PLUGINS_PREINSTALL_DISABLED=true
export GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false
export GF_PLUGINS_PUBLIC_KEY_RETRIEVAL_DISABLED=true
export GF_PLUGINS_PLUGIN_ADMIN_ENABLED=false
grafana server --homepath "$ROMA_GRAFANA_HOME" --config "$PWD/configs/observability/grafana.ini"
```

Use absolute `GF_PATHS_*` overrides: Grafana resolves relative paths against its installation home, and `cfg:default.paths.*` does not override explicit `[paths]` values in this INI. Keep the previously supplied `GF_SECURITY_ADMIN_PASSWORD` private. The plugin overrides prevent automatic installation/update/key fetching for this local lab.

Open `http://127.0.0.1:3000`, sign in using the supplied local administrator secret, and open **Roma / Roma measured operations**. The datasource points only at loopback Prometheus. A different app port requires editing the scrape target. Stop each foreground process with Ctrl-C. All generated database/log/TSDB state stays in ignored `var/roma`.

`GET /metrics` accepts socket loopback or the normal live administrator principal. Forwarded headers are not authority inside this endpoint. Disable Uvicorn proxy headers for a direct local lab (`--no-proxy-headers`). A reverse proxy must protect/block `/metrics` and preserve the actual peer boundary: forwarding every remote caller through a loopback socket would otherwise inherit loopback scrape access.

Dashboards show only observed samples and measured histogram quantiles. Endpointing is Pipecat's reported VAD quiet window; turn latency runs from VAD confirmation/context admission to software playback complete. Neither is acoustic end-to-end latency. First-token/first-audio measurements require nonempty outputs; interruption cancels pending stages. Queue gauges aggregate bounded active channels; histogram counts/sums survive for the process lifetime. CPU is user+system cumulative seconds and memory is process high-water RSS; current RSS/GPU are absent until independently collected. Metrics contain only reviewed stage/outcome/route/queue names, never query text, call IDs or secrets.

OpenTelemetry is optional and `TRACING_ENABLED=false` by default. Explicitly provision the `observability` extra before enabling it; disabled startup neither imports the SDK nor configures a tracer. With `TRACING_ENABLED=true`, the application lifespan creates a private SDK provider and a bounded 256-entry in-memory metadata processor available as `app.state.recent_trace_spans`. It retains only reviewed stage/route/outcome, generated trace/span/parent IDs and numeric times; shutdown clears it and restores the previous tracer. No trace API or disk persistence is added. Nothing installs a global provider, constructs a network exporter or interprets OTEL exporter environment variables. The lower-level `configure_tracing(enabled=True, processor=...)` remains available for an explicitly supplied approved processor. Frame spans end on first observed output or cancellation, with fixed outcome/route attributes and exception recording disabled.

Configuration references: [Prometheus configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/) and [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/). Native service readiness requires a separate operator run against installed binaries; JSON/YAML structural tests alone do not prove that acceptance gate.

## Verified synthetic native lab — 2026-10-26 UTC

The owner-authorized isolated lab used official Apple Silicon Prometheus **3.15.1** and Grafana OSS **13.2.4**. Downloads were pinned and matched the SHA-256 values on the [official Prometheus download page](https://prometheus.io/download/) and [official Grafana OSS version page](https://grafana.com/grafana/download/13.2.4?edition=oss&platform=mac), with archives/extracted tools retained only under ignored `var/roma/tools`. No global package manager or container was used.

[The minimized native receipt](../../benchmarks/telemetry/native-20261026.json) records all 22 checks passing: native version commands, promtool source/lab config and rendered-metric validation, Prometheus readiness and actual `up`/Roma counter/histogram/queue queries, Grafana health, the 10-panel provisioned dashboard, loopback datasource health and private-access/update settings. The fixture supplied 54 synthetic metric sample lines, including call count 3, turn-observation count 2 and queue depth 2. Ports and state were fresh and isolated; the random Admin password stayed in an ignored mode-0600 file. Both owned native processes and their listeners were verified stopped.

The initial native checks exposed missing metric HELP metadata and ineffective default-path overrides; those defects were corrected before the passing run. This receipt is explicitly synthetic: it does not show real-call stage data, visual browser rendering, acoustic performance, native speech/GPU capacity, multi-worker aggregation or production readiness. No application call, model, microphone, paid provider or working database was used, and no full roadmap gate passed.
