import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Modal } from "./ui";

/**
 * Numbers about everything played here, worked out from what's stored (app/routes/stats.py).
 * Charts are plain SVG: the server makes no outside calls to draw a page, so no chart library.
 * Single series use the accent; the three standing scores use three hues validated against
 * ink-900 for colour-blind separation, and always carry a legend.
 */
const SERIES = { trust: "#3987e5", affection: "#d95926", respect: "#199e70" };

function zone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}

const fmt = (n) => (n ?? 0).toLocaleString();

export default function StatsPanel({ open, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    api.stats(zone()).then(setData).catch((e) => setError(e.message));
  }, [open]);

  const t = data?.tiles;
  return (
    <Modal open={open} onClose={onClose} title="Stats" width={880}>
      {error && <p className="text-sm text-rose-300">{error}</p>}
      {!data && !error && <p className="text-sm text-slate-500">Counting…</p>}
      {data && (
        <div className="space-y-8">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Tile label="Words you've written" value={fmt(t.your_words)} note={`${fmt(t.your_messages)} messages`} />
            <Tile label="Words written back" value={fmt(t.their_words)} note={`about ${fmt(t.average_reply_words)} a reply`} />
            <Tile label="Days played" value={fmt(t.days_played)} note={`best streak ${t.best_streak}, now ${t.current_streak}`} />
            <Tile label="Chats" value={fmt(t.chats)} note={`${t.world_chats} in a world`} />
            <Tile label="Characters" value={fmt(t.characters)} note={`plus ${t.extras} extras`} />
            <Tile label="People met on walks" value={fmt(t.people_met)} note={`${t.people_remembering_you} remember you`} />
            <Tile label="Swipes" value={fmt(t.swipes)} note="other versions tried" />
            <Tile label="Journal entries" value={fmt(t.journal_entries)} note={`${fmt(t.narrator_lines)} narrator lines`} />
          </div>

          <Section title="Your last 60 days" hint="messages you sent each day">
            <Bars rows={data.by_day} labelEvery={10} format={(l) => shortDate(l)} unit="messages" />
          </Section>

          <div className="grid sm:grid-cols-2 gap-8">
            <Section title="When you play" hint="by hour of the day, your time">
              <Bars rows={data.by_hour} labelEvery={6} format={(h) => hourName(h)} unit="messages" />
            </Section>
            <Section title="Which days" hint="by day of the week">
              <Bars rows={data.by_weekday} labelEvery={1} unit="messages" />
            </Section>
          </div>

          <div className="grid sm:grid-cols-2 gap-8">
            <Section title="Who you talk to most" hint="replies written">
              <HBars rows={data.top_characters} unit="replies" />
            </Section>
            <Section title="Wordiest characters" hint="average words a reply, 3 replies or more">
              <HBars rows={data.wordiest_characters} unit="words" />
            </Section>
          </div>

          <Section title="Closest to you" hint="trust, affection and respect, from their best chat (0 to 100)">
            <Standing rows={data.standing} />
          </Section>

          <div className="grid sm:grid-cols-2 gap-8">
            <Section title="Words you use most" hint="leaving out the little ones">
              <HBars rows={data.your_words_most_used} unit="times" />
            </Section>
            <Section title="Narrator habits" hint="how often characters do these">
              <HBars rows={data.tics} unit="times" empty="No smirks yet. Give it time." />
            </Section>
          </div>

          <div className="grid sm:grid-cols-2 gap-8">
            <Section title="World chat meetings" hint="by world">
              <HBars rows={data.meetings_by_world} unit="meetings" empty="No world chats yet." />
            </Section>
            <Section title="Favourite places" hint="where meetings happened">
              <HBars rows={data.places} unit="meetings" empty="Nowhere yet." />
            </Section>
          </div>

          {(data.longest_reply || data.most_swiped) && (
            <Section title="Records">
              <div className="space-y-3 text-sm">
                {data.longest_reply && (
                  <p className="text-slate-300">
                    <span className="text-slate-100">Longest reply:</span> {fmt(data.longest_reply.words)} words from{" "}
                    {data.longest_reply.name} in “{data.longest_reply.chat}”.{" "}
                    <span className="text-slate-500 italic">{data.longest_reply.opening}…</span>
                  </p>
                )}
                {data.most_swiped && (
                  <p className="text-slate-300">
                    <span className="text-slate-100">Most re-rolled:</span> a reply from {data.most_swiped.name} in “
                    {data.most_swiped.chat}”, tried {data.most_swiped.swipes} ways.
                  </p>
                )}
              </div>
            </Section>
          )}
        </div>
      )}
    </Modal>
  );
}

function shortDate(iso) {
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function hourName(h) {
  return h === 0 ? "12am" : h < 12 ? `${h}am` : h === 12 ? "12pm" : `${h - 12}pm`;
}

function Tile({ label, value, note }) {
  return (
    <div className="border border-ink-800 rounded-lg px-3 py-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="font-serif text-2xl text-slate-100 mt-1">{value}</div>
      {note && <div className="text-xs text-slate-500 mt-0.5">{note}</div>}
    </div>
  );
}

function Section({ title, hint, children }) {
  return (
    <section>
      <h3 className="font-serif text-base text-slate-100">{title}</h3>
      {hint && <p className="text-xs text-slate-500 mb-3">{hint}</p>}
      {children}
    </section>
  );
}

/** A tooltip that follows the hovered mark, inside the chart's own box. */
function useTip() {
  const [tip, setTip] = useState(null);
  const box = useRef(null);
  function show(e, text) {
    const r = box.current?.getBoundingClientRect();
    if (r) setTip({ x: e.clientX - r.left, y: e.clientY - r.top, text });
  }
  const el = tip && (
    <div
      className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full px-2 py-1 rounded bg-ink-950 border border-ink-700 text-xs text-slate-200 whitespace-nowrap"
      style={{ left: tip.x, top: tip.y - 8 }}
    >
      {tip.text}
    </div>
  );
  return { box, show, hide: () => setTip(null), el };
}

/** Vertical bars on one axis, for counts over time. */
/**
 * The drawing is as wide as its box in pixels, so labels stay 11px on a phone and on a wide
 * screen alike, and they thin out to roughly one per 56px.
 */
function Bars({ rows, labelEvery = 1, format = (l) => l, unit }) {
  const { box, show, hide, el } = useTip();
  const [W, setW] = useState(600);
  useEffect(() => {
    const node = box.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(([e]) => setW(Math.max(200, Math.round(e.contentRect.width))));
    watch.observe(node);
    return () => watch.disconnect();
  }, [box]);
  labelEvery = Math.max(labelEvery, Math.ceil((rows.length * 56) / W));
  const max = Math.max(1, ...rows.map((r) => r.value));
  const H = W < 400 ? 110 : 140;
  const slot = W / rows.length;
  const bar = Math.max(2, slot - 2);
  if (!rows.some((r) => r.value))
    return <p className="text-sm text-slate-500">Nothing here yet.</p>;
  return (
    <div ref={box} className="relative" onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H + 22}`} className="w-full h-auto" role="img" aria-label={`Bar chart, peak ${max} ${unit}`}>
        <line x1="0" x2={W} y1={H} y2={H} stroke="#3A3227" strokeWidth="1" />
        {/* The peak, labelled at the left where no bar can sit under it. */}
        <text x="0" y="10" fontSize="11" fill="#ADA190">
          {max}
        </text>
        <line x1="28" x2={W} y1="6" y2="6" stroke="#2E2820" strokeWidth="1" strokeDasharray="2 4" />
        {rows.map((r, i) => {
          const h = (r.value / max) * (H - 8);
          return (
            <g key={i}>
              {/* The hit target is the whole column, taller and wider than the bar. */}
              <rect
                x={i * slot}
                y="0"
                width={slot}
                height={H}
                fill="transparent"
                onMouseMove={(e) => show(e, `${format(r.label)}: ${r.value} ${unit}`)}
              />
              {r.value > 0 && (
                <path
                  d={roundedTop(i * slot + (slot - bar) / 2, H - h, bar, h)}
                  fill="#D9A766"
                  pointerEvents="none"
                />
              )}
              {i % labelEvery === 0 && (
                <text
                  x={i === 0 ? 0 : i * slot + slot / 2}
                  y={H + 16}
                  textAnchor={i === 0 ? "start" : "middle"}
                  fontSize="11"
                  fill="#ADA190"
                >
                  {format(r.label)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {el}
      <TableView rows={rows} format={format} unit={unit} />
    </div>
  );
}

/** A bar whose data end is rounded and whose base sits square on the axis. */
function roundedTop(x, y, w, h) {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
}

/** Horizontal bars, for rankings with names to read. */
function HBars({ rows, unit, empty = "Nothing here yet." }) {
  const { box, show, hide, el } = useTip();
  if (!rows?.length) return <p className="text-sm text-slate-500">{empty}</p>;
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div ref={box} className="relative space-y-1.5" onMouseLeave={hide}>
      {rows.map((r) => (
        <div
          key={r.label}
          className="grid grid-cols-[8rem_1fr_3rem] items-center gap-2 text-xs"
          onMouseMove={(e) => show(e, `${r.label}: ${r.value.toLocaleString()} ${unit}`)}
        >
          <span className="truncate text-slate-300" title={r.label}>
            {r.label}
          </span>
          <span className="h-3 block">
            <span
              className="block h-full rounded-r bg-accent"
              style={{ width: `${Math.max(2, (r.value / max) * 100)}%` }}
            />
          </span>
          <span className="text-right text-slate-400 tabular-nums">{r.value.toLocaleString()}</span>
        </div>
      ))}
      {el}
    </div>
  );
}

/** Three scores per character, grouped, with a legend: identity never rests on colour alone. */
function Standing({ rows }) {
  const { box, show, hide, el } = useTip();
  if (!rows?.length) return <p className="text-sm text-slate-500">Nobody has formed an opinion of you yet.</p>;
  return (
    <div ref={box} className="relative" onMouseLeave={hide}>
      <div className="flex gap-4 text-xs text-slate-400 mb-3">
        {Object.entries(SERIES).map(([k, c]) => (
          <span key={k} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: c }} />
            {k}
          </span>
        ))}
      </div>
      <div className="space-y-3">
        {rows.map((r) => (
          <div key={r.name} className="grid grid-cols-[8rem_1fr] gap-2 items-center text-xs">
            <span className="truncate text-slate-300">{r.name}</span>
            <div className="space-y-0.5">
              {Object.entries(SERIES).map(([k, c]) => (
                <div
                  key={k}
                  className="h-2"
                  onMouseMove={(e) => show(e, `${r.name}, ${k}: ${r[k]} of 100`)}
                >
                  <div className="h-full rounded-r" style={{ width: `${Math.max(1, r[k])}%`, background: c }} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      {el}
    </div>
  );
}

/** The same numbers as text, for screen readers and anyone who'd rather read than squint. */
function TableView({ rows, format, unit }) {
  return (
    <details className="mt-1 text-xs text-slate-500">
      <summary className="cursor-pointer hover:text-slate-300 w-fit">as a table</summary>
      <div className="mt-2 max-h-40 overflow-y-auto scroll-thin columns-2 sm:columns-3">
        {rows.map((r, i) => (
          <div key={i} className="flex justify-between gap-3 pr-4">
            <span>{format(r.label)}</span>
            <span className="tabular-nums text-slate-300">
              {r.value} {unit}
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}
