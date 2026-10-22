/** What a Server Action hands back to its form: a message, and at most one secret shown once. */
export interface ActionState {
  ok: boolean;
  message: string;
  secret?: string;
}

export const idle: ActionState = { ok: true, message: "" };
