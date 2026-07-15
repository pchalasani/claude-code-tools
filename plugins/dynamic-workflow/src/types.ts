export type JsonPrimitive = boolean | number | string | null;

export type JsonValue =
  | JsonPrimitive
  | JsonValue[]
  | { [key: string]: JsonValue };

export type SandboxMode =
  | "read-only"
  | "workspace-write"
  | "danger-full-access";

export type ReasoningEffort =
  | "none"
  | "minimal"
  | "low"
  | "medium"
  | "high"
  | "xhigh";

export interface AgentOptions {
  addDirs?: string[];
  cacheKey?: unknown;
  cwd?: string;
  id?: string;
  ignoreUserConfig?: boolean;
  label?: string;
  model?: string;
  reasoningEffort?: ReasoningEffort;
  resumeThreadId?: string;
  retries?: number;
  sandbox?: SandboxMode;
  schema?: Record<string, unknown>;
  timeoutMs?: number;
}

export interface PipelineOptions<T> {
  concurrency?: number;
  key?: (item: T, index: number) => string;
  label?: string;
  maxItems?: number;
}

export interface WorkflowMeta {
  description?: string;
  name?: string;
}

export type AgentFunction = <T = string>(
  prompt: string,
  options?: AgentOptions,
) => Promise<T>;

export type PipelineFunction = <T, R>(
  items: readonly T[],
  worker: (item: T, index: number) => Promise<R>,
  options?: PipelineOptions<T>,
) => Promise<R[]>;

export type ParallelFunction = <T>(
  tasks: ReadonlyArray<() => Promise<T>>,
  options?: { concurrency?: number; label?: string },
) => Promise<T[]>;

export interface WorkflowApi {
  agent: AgentFunction;
  args: unknown;
  checkpoint: () => Promise<void>;
  log: (...values: unknown[]) => Promise<void>;
  parallel: ParallelFunction;
  pipeline: PipelineFunction;
  runId: string;
}

export type RunStatus =
  | "starting"
  | "running"
  | "pausing"
  | "paused"
  | "canceling"
  | "canceled"
  | "completed"
  | "failed";

export type StepStatus = "running" | "completed" | "failed" | "canceled";

export interface TokenUsage {
  cachedInputTokens?: number;
  inputTokens?: number;
  outputTokens?: number;
}

export interface AgentStep {
  attempt: number;
  completedAt?: string;
  error?: string;
  fingerprint: string;
  id: string;
  label: string;
  result?: JsonValue;
  startedAt: string;
  status: StepStatus;
  threadId?: string;
  usage?: TokenUsage;
  workerPid?: number;
  workerStartedAt?: string;
}

export interface RunState {
  agentInvocations?: number;
  args?: JsonValue;
  authorization?: RunAuthorization;
  cleanupPending?: boolean;
  completedAt?: string;
  concurrency: number;
  createdAt: string;
  cwd: string;
  defaultAgentTimeoutMs?: number;
  enginePid?: number;
  engineStartedAt?: string;
  error?: string;
  maxAgentInvocations?: number;
  maxRuntimeMs?: number;
  pid?: number;
  pidStartedAt?: string;
  result?: JsonValue;
  runnerHash?: string;
  runnerStartedAt?: string;
  runId: string;
  startedAt?: string;
  status: RunStatus;
  steps: Record<string, AgentStep>;
  terminalFingerprint?: string;
  updatedAt: string;
  version: 1;
  workflowHash: string;
  workflowPath: string;
}

export type ControlCommand = "run" | "pause" | "cancel";

export interface RunAuthorization {
  dangerFullAccess: boolean;
  workflowHash: string;
  workspaceWrite: boolean;
}

export interface RunControl {
  authorization?: RunAuthorization;
  command: ControlCommand;
  updatedAt: string;
}

export interface CodexExecution {
  data?: JsonValue;
  text: string;
  threadId?: string;
  usage?: TokenUsage;
}

export interface CodexRequest {
  defaultTimeoutMs: number;
  options: AgentOptions;
  prompt: string;
  runDirectory: string;
  signal: AbortSignal;
  stepId: string;
  workflowCwd: string;
}
