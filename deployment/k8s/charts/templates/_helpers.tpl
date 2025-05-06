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
Database URL construction helper
*/}}
{{- define "database.url" -}}
{{- if eq .Values.database.type "json" -}}
{{ .Values.database.json.path }}
{{- else if eq .Values.database.type "duckdb" -}}
{{ .Values.database.duckdb.path }}
{{- else if eq .Values.database.type "postgresql" -}}
{{- if .Values.database.external.enabled -}}
{{- $externalPassword := .Values.database.external.password -}}
{{- if .Values.database.external.existingSecret -}}
{{- $secret := (lookup "v1" "Secret" .Release.Namespace .Values.database.external.existingSecret) -}}
{{- if and $secret $secret.data (hasKey $secret.data .Values.database.external.existingSecretKey) -}}
{{- $externalPassword = index $secret.data .Values.database.external.existingSecretKey | b64dec -}}
{{- end -}}
{{- end -}}
postgresql://{{ .Values.database.external.user }}:{{ $externalPassword }}@{{ .Values.database.external.host }}:{{ .Values.database.external.port }}/{{ .Values.database.external.database }}
{{- else -}}
{{- $postgresqlPassword := (randAlphaNum 32) -}}
{{- if .Values.postgresql.auth.password -}}
{{- $postgresqlPassword = .Values.postgresql.auth.password -}}
{{- else -}}
{{- $postgresqlSecret := (lookup "v1" "Secret" .Release.Namespace (printf "%s-postgresql" (include "titiler.fullname" .))) -}}
{{- $passwordKey := printf "password" -}}
{{- if and $postgresqlSecret $postgresqlSecret.data (hasKey $postgresqlSecret.data $passwordKey) -}}
{{- $postgresqlPassword = index $postgresqlSecret.data $passwordKey | b64dec -}}
{{- end -}}
{{- end -}}
postgresql://{{ .Values.postgresql.auth.username }}:{{ $postgresqlPassword }}@{{ include "titiler.fullname" . }}-postgresql:5432/{{ .Values.postgresql.auth.database }}
{{- end -}}
{{- end -}}
{{- end -}}
