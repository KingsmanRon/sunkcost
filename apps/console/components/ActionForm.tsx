"use client";

import { useActionState, type ReactNode } from "react";

import { idle, type ActionState } from "@/lib/action-state";

type Action = (state: ActionState, form: FormData) => Promise<ActionState>;

/** A form bound to a Server Action, showing its pending state, result and any one-time secret. */
export function ActionForm({
  action,
  submit,
  children,
  hidden = {},
  disabled = false,
  confirm,
  variant = "primary",
  inline = false,
}: {
  action: Action;
  submit: string;
  children?: ReactNode;
  hidden?: Record<string, string>;
  disabled?: boolean;
  confirm?: string;
  variant?: "primary" | "secondary" | "danger";
  inline?: boolean;
}) {
  const [state, formAction, pending] = useActionState(action, idle);
  return (
    <form
      action={formAction}
      className={inline ? "form inline" : "form"}
      onSubmit={(event) => {
        if (confirm && !window.confirm(confirm)) event.preventDefault();
      }}
    >
      {Object.entries(hidden).map(([name, value]) => (
        <input key={name} type="hidden" name={name} value={value} />
      ))}
      {children}
      <button type="submit" className={variant} disabled={disabled || pending}>
        {pending ? "Working…" : submit}
      </button>
      {state.message ? (
        <p role="status" className={state.ok ? "note ok" : "note error"}>
          {state.message}
        </p>
      ) : null}
      {state.secret ? (
        <output className="secret" aria-label="secret">
          {state.secret}
        </output>
      ) : null}
    </form>
  );
}
