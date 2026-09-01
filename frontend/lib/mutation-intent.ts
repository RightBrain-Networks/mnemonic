"use client";

import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useSyncExternalStore,
  type ReactNode
} from "react";
import {
  classifyMutationResponse,
  type FrozenMutationRequest,
  type MutationHttpOutcome,
  type MutationKind,
  type MutationResultByKind
} from "./mutation-responses.ts";

export type MutationIntentState = "prepared" | "in_flight" | "unresolved" | "safety_conflict";

export interface MutationIntent<K extends MutationKind = MutationKind> extends FrozenMutationRequest {
  readonly kind: K;
  readonly slot: string;
  readonly projectId: string;
  readonly conflictKeys: readonly string[];
  readonly state: MutationIntentState;
  readonly attempts: number;
  readonly message: string | null;
}

export interface MutationIntentSummary {
  readonly kind: MutationKind;
  readonly slot: string;
  readonly projectId: string;
  readonly conflictKeys: readonly string[];
  readonly state: MutationIntentState;
  readonly attempts: number;
  readonly message: string | null;
}

export interface PrepareMutation<K extends MutationKind> {
  readonly kind: K;
  readonly slot: string;
  readonly projectId: string;
  readonly conflictKeys: readonly string[];
  readonly method: "POST" | "PATCH" | "DELETE";
  readonly path: string;
  readonly payload: object;
}

export class MutationIntentError extends Error {
  readonly state: "unresolved" | "safety_conflict" | "blocked";
  readonly slot?: string;

  constructor(message: string, state: MutationIntentError["state"], slot?: string) {
    super(message);
    this.name = "MutationIntentError";
    this.state = state;
    this.slot = slot;
  }
}

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type UuidFactory = () => string;
type Listener = () => void;
type RecoveryListener = (intent: MutationIntentSummary) => void;

const UNKNOWN_OUTCOME = "The mutation outcome is unknown. Retry the same pending action.";
const SAFETY_CONFLICT = "Mnemonic could not match this retry to its original request. This action remains blocked; stop and inspect the client or server state before continuing.";
const BLOCKED = "A related mutation has an unresolved outcome. Resolve that pending action before making another change.";
const MUTATION_REQUEST_DEADLINE_MS = 20_000;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function immutableIntent<K extends MutationKind>(intent: MutationIntent<K>): MutationIntent<K> {
  return Object.freeze({
    ...intent,
    conflictKeys: Object.freeze([...intent.conflictKeys])
  });
}

function publicSummary(intent: MutationIntent): MutationIntentSummary {
  return Object.freeze({
    kind: intent.kind,
    slot: intent.slot,
    projectId: intent.projectId,
    conflictKeys: intent.conflictKeys,
    state: intent.state,
    attempts: intent.attempts,
    message: intent.message
  });
}

function intersects(left: readonly string[], right: readonly string[]): boolean {
  const values = new Set(left);
  return right.some((key) => values.has(key));
}

function sameKeys(left: readonly string[], right: readonly string[]): boolean {
  const values = new Set(left);
  return left.length === right.length
    && new Set(right).size === right.length
    && right.every((key) => values.has(key));
}

export function mutationProjectKey(projectId: string): string {
  return `project:${projectId.toLowerCase()}`;
}

export function mutationWorkKey(projectId: string, workItemId: string): string {
  return `work:${projectId.toLowerCase()}:${workItemId.toLowerCase()}`;
}

export function mutationCreateKey(projectId: string): string {
  return `work-create:${projectId.toLowerCase()}`;
}

export class MutationIntentRegistry {
  readonly #fetcher: Fetcher;
  readonly #uuidFactory: UuidFactory;
  readonly #requestDeadlineMs: number;
  readonly #intents = new Map<string, MutationIntent>();
  readonly #listeners = new Set<Listener>();
  readonly #recoveryListeners = new Set<RecoveryListener>();
  readonly #inFlight = new Map<string, Promise<unknown>>();
  #snapshot: readonly MutationIntentSummary[] = Object.freeze([]);

  constructor(
    fetcher: Fetcher = (input, init) => fetch(input, init),
    uuidFactory: UuidFactory = () => crypto.randomUUID(),
    requestDeadlineMs = MUTATION_REQUEST_DEADLINE_MS
  ) {
    if (!Number.isInteger(requestDeadlineMs) || requestDeadlineMs <= 0) {
      throw new Error("The mutation request deadline must be a positive integer.");
    }
    this.#fetcher = fetcher;
    this.#uuidFactory = uuidFactory;
    this.#requestDeadlineMs = requestDeadlineMs;
  }

  readonly subscribe = (listener: Listener): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  readonly getSnapshot = (): readonly MutationIntentSummary[] => this.#snapshot;

  readonly subscribeRecovered = (listener: RecoveryListener): (() => void) => {
    this.#recoveryListeners.add(listener);
    return () => this.#recoveryListeners.delete(listener);
  };

  get(slot: string): MutationIntent | undefined {
    return this.#intents.get(slot);
  }

  hasDispatched(): boolean {
    return [...this.#intents.values()].some((intent) => intent.state !== "prepared");
  }

  hasDispatchedForProject(projectId: string): boolean {
    return [...this.#intents.values()].some((intent) => (
      intent.projectId.toLowerCase() === projectId.toLowerCase() && intent.state !== "prepared"
    ));
  }

  blocks(conflictKeys: readonly string[], exceptSlot?: string): boolean {
    return [...this.#intents.values()].some((intent) => (
      intent.slot !== exceptSlot
      && intent.state !== "prepared"
      && intersects(intent.conflictKeys, conflictKeys)
    ));
  }

  prepare<K extends MutationKind>(input: PrepareMutation<K>): MutationIntent<K> {
    const existing = this.#intents.get(input.slot);
    if (existing && existing.state !== "prepared") {
      throw new MutationIntentError(BLOCKED, "blocked", existing.slot);
    }
    if (this.blocks(input.conflictKeys, input.slot)) {
      throw new MutationIntentError(BLOCKED, "blocked");
    }
    if (!input.conflictKeys.length || new Set(input.conflictKeys).size !== input.conflictKeys.length) {
      throw new Error("Mutation conflict keys must be nonempty and unique.");
    }
    const operationId = this.#uuidFactory();
    if (!UUID_PATTERN.test(operationId)) {
      throw new Error("The mutation operation ID generator returned an invalid UUID.");
    }
    const body = JSON.stringify({ ...input.payload, client_operation_id: operationId });
    const intent = immutableIntent({
      kind: input.kind,
      slot: input.slot,
      projectId: input.projectId,
      conflictKeys: input.conflictKeys,
      method: input.method,
      path: input.path,
      operationId,
      body,
      state: "prepared",
      attempts: 0,
      message: null
    });
    this.#intents.set(input.slot, intent);
    this.#emit();
    return intent;
  }

  discardPrepared(slot: string): boolean {
    const intent = this.#intents.get(slot);
    if (!intent || intent.state !== "prepared") return false;
    this.#intents.delete(slot);
    this.#emit();
    return true;
  }

  async execute<K extends MutationKind>(input: PrepareMutation<K>): Promise<MutationResultByKind[K]> {
    const existing = this.#intents.get(input.slot);
    if (!existing) return this.dispatch(this.prepare(input));
    if (
      existing.kind !== input.kind
      || existing.projectId !== input.projectId
      || existing.method !== input.method
      || existing.path !== input.path
      || !sameKeys(existing.conflictKeys, input.conflictKeys)
      || JSON.stringify({ ...input.payload, client_operation_id: existing.operationId }) !== existing.body
    ) {
      throw new MutationIntentError(BLOCKED, "blocked", existing.slot);
    }
    if (existing.state === "safety_conflict") {
      throw new MutationIntentError(SAFETY_CONFLICT, "safety_conflict", existing.slot);
    }
    if (existing.state === "unresolved" || existing.state === "prepared") {
      return this.dispatch(existing as MutationIntent<K>);
    }
    const inFlight = this.#inFlight.get(existing.slot);
    if (!inFlight) throw new MutationIntentError(UNKNOWN_OUTCOME, "unresolved", existing.slot);
    return inFlight as Promise<MutationResultByKind[K]>;
  }

  async retry<K extends MutationKind>(slot: string): Promise<MutationResultByKind[K]> {
    const intent = this.#intents.get(slot);
    if (!intent) throw new Error("The pending mutation is no longer available.");
    if (intent.state === "safety_conflict") {
      throw new MutationIntentError(SAFETY_CONFLICT, "safety_conflict", slot);
    }
    if (intent.state === "in_flight") {
      const pending = this.#inFlight.get(slot);
      if (!pending) throw new MutationIntentError(UNKNOWN_OUTCOME, "unresolved", slot);
      return pending as Promise<MutationResultByKind[K]>;
    }
    return this.dispatch(intent as MutationIntent<K>, true);
  }

  private async dispatch<K extends MutationKind>(
    intent: MutationIntent<K>,
    recovered = false
  ): Promise<MutationResultByKind[K]> {
    const dispatched = immutableIntent({
      ...intent,
      state: "in_flight",
      attempts: intent.attempts + 1,
      message: null
    });
    this.#intents.set(intent.slot, dispatched);
    this.#emit();
    const request = this.#send(dispatched, recovered);
    this.#inFlight.set(intent.slot, request);
    try {
      return await request;
    } finally {
      if (this.#inFlight.get(intent.slot) === request) this.#inFlight.delete(intent.slot);
    }
  }

  async #send<K extends MutationKind>(
    intent: MutationIntent<K>,
    recovered: boolean
  ): Promise<MutationResultByKind[K]> {
    const controller = new AbortController();
    let deadlineHandle: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_resolve, reject) => {
      deadlineHandle = setTimeout(() => {
        controller.abort();
        reject(new Error("The mutation request deadline expired."));
      }, this.#requestDeadlineMs);
    });
    let outcome: MutationHttpOutcome<K>;
    try {
      outcome = await Promise.race([
        (async () => {
          const response = await this.#fetcher(`/api/mnemonic${intent.path}`, {
            method: intent.method,
            body: intent.body,
            credentials: "same-origin",
            cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            signal: controller.signal
          });
          return classifyMutationResponse(intent, response);
        })(),
        deadline
      ]);
    } catch {
      this.#retain(intent, "unresolved", UNKNOWN_OUTCOME);
      throw new MutationIntentError(UNKNOWN_OUTCOME, "unresolved", intent.slot);
    } finally {
      if (deadlineHandle !== undefined) clearTimeout(deadlineHandle);
    }
    if (outcome.type === "success") {
      this.#intents.delete(intent.slot);
      this.#emit();
      if (recovered) {
        const summary = publicSummary(intent);
        for (const listener of this.#recoveryListeners) listener(summary);
      }
      return outcome.value;
    }
    if (outcome.type === "rejected") {
      this.#intents.delete(intent.slot);
      this.#emit();
      throw outcome.error;
    }
    if (outcome.type === "safety_conflict") {
      this.#retain(intent, "safety_conflict", SAFETY_CONFLICT);
      throw new MutationIntentError(SAFETY_CONFLICT, "safety_conflict", intent.slot);
    }
    this.#retain(intent, "unresolved", outcome.message);
    throw new MutationIntentError(outcome.message, "unresolved", intent.slot);
  }

  #retain(
    intent: MutationIntent,
    state: "unresolved" | "safety_conflict",
    message: string
  ): void {
    this.#intents.set(intent.slot, immutableIntent({ ...intent, state, message }));
    this.#emit();
  }

  #emit(): void {
    this.#snapshot = Object.freeze([...this.#intents.values()].map(publicSummary));
    for (const listener of this.#listeners) listener();
  }
}

const MutationIntentContext = createContext<MutationIntentRegistry | null>(null);

export function MutationIntentProvider({
  registry,
  children
}: {
  registry: MutationIntentRegistry;
  children: ReactNode;
}) {
  return createElement(MutationIntentContext.Provider, { value: registry }, children);
}

export function useMutationIntentRegistry(): MutationIntentRegistry {
  const registry = useContext(MutationIntentContext);
  if (!registry) throw new Error("MutationIntentProvider is missing.");
  return registry;
}

export function useMutationIntents(
  registry?: MutationIntentRegistry
): readonly MutationIntentSummary[] {
  const contextRegistry = useContext(MutationIntentContext);
  const selectedRegistry = registry ?? contextRegistry;
  if (!selectedRegistry) throw new Error("MutationIntentProvider is missing.");
  return useSyncExternalStore(
    selectedRegistry.subscribe,
    selectedRegistry.getSnapshot,
    selectedRegistry.getSnapshot
  );
}

export function useMutationUnloadWarning(registry: MutationIntentRegistry): void {
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!registry.hasDispatched()) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [registry]);
}
