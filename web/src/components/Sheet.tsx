import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { errorText } from "../lib/errors";
import { tr } from "../lib/i18n";
import { Icon } from "./Icons";

/** One small modal form: add an account, a new password, a confirmation, "my account". */
export interface SheetJob {
  title: string;
  body: ReactNode;
  submit?: string;
  danger?: boolean;
  /** Returns the message for the toast; throws to show an error in the form. */
  onSubmit?: () => Promise<string | void>;
  /** Closed without saving (cancel, ×, Escape). */
  onClose?: () => void;
}

export function Sheet({ job, onDone }: { job: SheetJob | null; onDone: (message?: string) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const current = useRef<SheetJob | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    current.current = job;
    setError("");
    setBusy(false);
    if (job) {
      if (!node.open) node.showModal();
      const first = body.current?.querySelector<HTMLElement>("input:not([type=radio]), select");
      first?.focus();
    } else if (node.open) {
      node.close();
    }
  }, [job]);

  function cancel() {
    const was = current.current;
    current.current = null;
    was?.onClose?.();
    onDone();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const was = job;
    if (!was?.onSubmit) return cancel();
    setBusy(true);
    setError("");
    try {
      const message = await was.onSubmit();
      if (current.current === was) {
        current.current = null;
        onDone(message || undefined);
      }
    } catch (err) {
      if (current.current === was) setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog id="sheet" className="sheet" aria-labelledby="sheet-title" ref={dialog}
            onClose={() => { if (current.current) cancel(); }}>
      <form id="sheet-form" noValidate onSubmit={submit}>
        <div className="sheet-head">
          <h2 id="sheet-title">{job?.title}</h2>
          <button id="sheet-x" className="button ghost" type="button" aria-label={tr("إغلاق", "Close")} onClick={cancel}>
            <Icon name="close" />
          </button>
        </div>
        <div id="sheet-body" className="sheet-body" ref={body}>{job?.body}</div>
        <p id="sheet-error" className="form-error" role="alert" hidden={!error}>{error}</p>
        <div className="sheet-foot">
          <button id="sheet-submit" className={"button " + (job?.danger ? "danger" : "primary")} type="submit"
                  hidden={!job?.onSubmit} disabled={busy}>
            {job?.submit || tr("حفظ", "Save")}
          </button>
          <button id="sheet-cancel" className="button" type="button" onClick={cancel}>
            {job?.onSubmit ? tr("إلغاء", "Cancel") : tr("إغلاق", "Close")}
          </button>
        </div>
      </form>
    </dialog>
  );
}
