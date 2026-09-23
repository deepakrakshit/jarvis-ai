// Gateway Protocol artifacts schema
// Substrate origin: packages/gateway-protocol/src/schema/artifacts.ts

export type ArtifactDownloadMode = "bytes" | "url" | "unsupported";

export interface ArtifactSummary {
  id: string;
  type: string;
  title: string;
  mimeType?: string;
  sizeBytes?: number;
  sessionKey?: string;
  runId?: string;
  taskId?: string;
  source?: string;
  download: {
    mode: ArtifactDownloadMode;
  };
}

export interface ArtifactsListParams {
  sessionKey?: string;
  runId?: string;
  taskId?: string;
  agentId?: string;
  type?: string;
  limit?: number;
  cursor?: string;
}

export interface ArtifactsListResult {
  artifacts: ArtifactSummary[];
  nextCursor?: string;
  omittedOversized?: boolean;
}

export interface ArtifactsGetParams {
  artifactId: string;
  sessionKey?: string;
  taskId?: string;
}

export interface ArtifactsGetResult {
  artifact: ArtifactSummary;
}

export interface ArtifactsDownloadParams {
  artifactId: string;
  sessionKey?: string;
  taskId?: string;
}

export interface ArtifactsDownloadResult {
  artifact: ArtifactSummary;
  encoding?: "base64" | "utf-8";
  data?: string;
  url?: string;
  expiresAt?: string;
}
