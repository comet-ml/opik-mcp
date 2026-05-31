{{- define "opik-mcp.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "opik-mcp.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- include "opik-mcp.name" . -}}
{{- end -}}
{{- end -}}

{{- define "opik-mcp.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "opik-mcp.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "opik-mcp.labels" -}}
app.kubernetes.io/name: {{ include "opik-mcp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "opik-mcp.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opik-mcp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
