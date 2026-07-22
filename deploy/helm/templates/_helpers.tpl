{{- define "rhdp-rca.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "rhdp-rca.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "rhdp-rca.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.registry }}/{{ .Release.Namespace }}/{{ .Values.image.name }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.registry }}/{{ .Release.Namespace }}/{{ .Values.image.name }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}
