{{/* vim: set filetype=mustache: */}}
{{/*
Expand the name of the chart.
*/}}
{{- define "titiler.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "titiler.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "titiler.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels
*/}}
{{- define "titiler.labels" -}}
helm.sh/chart: {{ include "titiler.chart" . }}
{{ include "titiler.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "titiler.selectorLabels" -}}
app.kubernetes.io/name: {{ include "titiler.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Name of the optional bundled postgresql StatefulSet / Service / Secret.
Keeping the suffix as `-postgresql` preserves DSN compatibility with
prior chart versions that used the bitnami subchart (which generated
the same Service name).
*/}}
{{- define "titiler.postgresql.fullname" -}}
{{ include "titiler.fullname" . }}-postgresql
{{- end -}}

{{/*
Database URL construction helper.

For postgresql backends the password MUST be supplied at render time
(via values) or the full TITILER_OPENEO_STORE_URL must be injected from
a Secret using `.Values.envVars.fromSecret`. Relying on Helm `lookup` is
not GitOps-compatible: ArgoCD renders manifests with `helm template`
which has no live cluster access, so `lookup` returns empty and any
fallback (e.g. `randAlphaNum`) would produce a different password on
every sync.
*/}}
{{- define "database.url" -}}
{{- if eq .Values.database.type "json" -}}
{{ .Values.database.json.path }}
{{- else if eq .Values.database.type "duckdb" -}}
{{ .Values.database.duckdb.path }}
{{- else if eq .Values.database.type "postgresql" -}}
{{- if .Values.database.external.enabled -}}
{{- $externalPassword := .Values.database.external.password -}}
{{- if not $externalPassword -}}
{{- fail "database.external.password is required when database.type=postgresql and database.external.enabled=true. Either set it in values, or inject the full DSN via envVars.fromSecret with name TITILER_OPENEO_STORE_URL (which will bypass this helper)." -}}
{{- end -}}
postgresql://{{ .Values.database.external.user }}:{{ $externalPassword }}@{{ .Values.database.external.host }}:{{ .Values.database.external.port }}/{{ .Values.database.external.database }}
{{- else -}}
{{- $postgresqlPassword := .Values.postgresql.auth.password -}}
{{- if not $postgresqlPassword -}}
{{- fail "postgresql.auth.password is required when database.type=postgresql and the bundled postgresql subchart is used. Either set it in values, or inject the full DSN via envVars.fromSecret with name TITILER_OPENEO_STORE_URL (which will bypass this helper)." -}}
{{- end -}}
postgresql://{{ .Values.postgresql.auth.username }}:{{ $postgresqlPassword }}@{{ include "titiler.postgresql.fullname" . }}:5432/{{ .Values.postgresql.auth.database }}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Image of the API and of the schema Job, so the schema is always created by
the version being deployed.
*/}}
{{- define "titiler.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default (printf "titiler-openeo-%s" .Chart.AppVersion) }}
{{- end -}}

{{/*
Returns "true" when the user supplies TITILER_OPENEO_STORE_URL via
.Values.envVars.fromSecret. In that case the deployment skips the
helper-emitted value: env var so the runtime value comes from the
secretKeyRef instead. Returns an empty string otherwise.
*/}}
{{- define "database.urlFromSecret" -}}
{{- range .Values.envVars.fromSecret -}}
{{- if eq .name "TITILER_OPENEO_STORE_URL" -}}true{{- end -}}
{{- end -}}
{{- end -}}

{{/*
TITILER_OPENEO_STORE_URL and TITILER_OPENEO_TILE_STORE_URL built from
`database.url`, first in the env of the Deployment and of the schema Job.
Neither is rendered when the store URL comes from `.Values.envVars.fromSecret`.
An `env` or `envVars` entry of the same name comes later and wins.
*/}}
{{- define "titiler.storeEnv" -}}
{{- $urlFromSecret := eq (include "database.urlFromSecret" .) "true" }}
{{- if not $urlFromSecret }}
- name: TITILER_OPENEO_STORE_URL
  value: {{ include "database.url" . | quote }}
{{- end }}
{{- if and .Values.enableTileStore (not $urlFromSecret) }}
- name: TITILER_OPENEO_TILE_STORE_URL
  value: {{ include "database.url" . | quote }}
{{- end }}
{{- end -}}

{{/*
The literal `.Values.env` map, shared by the Deployment and the schema Job, so
a tile store URL or libpq variables (PGSSLMODE, ...) set there reach both.
TITILER_OPENEO_STORE_URL is skipped: it comes from `titiler.storeEnv`.
*/}}
{{- define "titiler.literalEnv" -}}
{{- range $key, $val := .Values.env }}
{{- if ne $key "TITILER_OPENEO_STORE_URL" }}
- name: {{ $key }}
  value: {{ $val | quote }}
{{- end }}
{{- end }}
{{- end -}}
