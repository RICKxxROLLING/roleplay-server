/** Shared primitives so every panel looks and behaves the same. */

export function Modal({ open, onClose, title, width = 560, children }) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 bg-black/60 grid place-items-center z-50 p-4"
      onClick={onClose}
    >
      <div
        style={{ width }}
        className="bg-ink-900 border border-ink-700 rounded-xl max-h-[85vh] overflow-y-auto scroll-thin"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-ink-800 flex items-center justify-between sticky top-0 bg-ink-900">
          <h2 className="font-semibold">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-slate-500 hover:text-slate-300 text-lg leading-none"
          >
            ×
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, hint, children }) {
  return (
    <div className="mb-4">
      <label className="text-sm block mb-1.5">
        {label}
        {hint && <span className="text-xs text-slate-600 ml-2">{hint}</span>}
      </label>
      {children}
    </div>
  );
}

const inputBase =
  "w-full bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 text-sm outline-none focus:border-accent/60 placeholder:text-slate-600";

export function Input(props) {
  return <input {...props} className={`${inputBase} ${props.className || ""}`} />;
}

export function Textarea(props) {
  return (
    <textarea
      {...props}
      className={`${inputBase} leading-relaxed resize-y ${props.className || ""}`}
    />
  );
}

export function Button({ variant = "ghost", className = "", ...props }) {
  const styles = {
    primary:
      "bg-accent/25 border border-accent/40 hover:bg-accent/35 disabled:opacity-40",
    ghost: "border border-ink-700 hover:bg-ink-850 disabled:opacity-40",
    danger:
      "border border-rose-900/60 text-rose-300 hover:bg-rose-950/40 disabled:opacity-40",
  };
  return (
    <button
      {...props}
      className={`text-sm px-3 py-1.5 rounded-lg transition ${styles[variant]} ${className}`}
    />
  );
}

export function Empty({ children }) {
  return <p className="text-sm text-slate-600 py-4 text-center">{children}</p>;
}
