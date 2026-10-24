import { useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip } from "react-leaflet";
import { api, type ScenarioSummary, type SimStrategy, type Simulation as SimulationResult } from "./api";

const CSS = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const BASELINE = "nearest_capable";
const ORDER = ["nearest_ed", "nearest_capable", "metropolis", "metropolis_swaps"];
const CATEGORY: Record<string, string> = {
  trauma: "Trauma", lvo: "Stroke (LVO)", stroke: "Stroke", stemi: "STEMI", burn: "Burn", peds: "Pediatric", ob: "Obstetric", general: "General",
};

function delta(a: number, b: number) {
  const d = Math.round((a - b) * 10) / 10;
  return d > 0 ? `${d} min faster` : d < 0 ? `${-d} min slower` : "same";
}

function StrategyCard({ s, base, maxMean, nameOf }: { s: SimStrategy; base?: SimStrategy; maxMean: number; nameOf: (id: string | null) => string }) {
  const m = s.metrics;
  const isBase = s.id === BASELINE;
  const metro = s.id.startsWith("metropolis");
  return (
    <article className={`sim-card${metro ? " metro" : ""}${isBase ? " base" : ""}`} aria-label={s.label}>
      <div className="sim-card-head">
        <h3>{s.label}</h3>
        {isBase && <span className="chip">Baseline</span>}
      </div>
      <div className="sim-big">{m.mean}<small>avg min to care</small></div>
      <div className="bar" aria-hidden="true"><i className={metro ? "d" : "w"} style={{ width: `${(m.mean / maxMean) * 100}%` }} /></div>
      <ul className="sim-stats">
        <li><span>Critical patients (acuity 1)</span><b>{m.acuity1_mean ?? "–"} min</b></li>
        <li><span>Median</span><b>{m.median} min</b></li>
        <li><span>Sent where they can't be treated</span><b className={m.not_capable ? "loss" : ""}>{m.not_capable}</b></li>
        <li><span>Busiest ED</span><b>{m.peak_en_route} ambulances · {nameOf(m.peak_hospital)}</b></li>
        {s.id === "metropolis_swaps" && <li><span>Swaps applied</span><b>{m.swaps}</b></li>}
      </ul>
      {s.id === "metropolis_swaps" && m.swaps === 0 && (
        <p className="rule">No swaps needed: no hospital got worse after units were sent. Swaps pay off when ED conditions change mid-transport.</p>
      )}
      {base && !isBase && (
        <p className={`sim-vs ${m.mean < base.metrics.mean ? "gain" : "loss"}`}>
          vs. today: {delta(base.metrics.mean, m.mean)} per patient
        </p>
      )}
    </article>
  );
}

export default function Simulation() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenario, setScenario] = useState("surge");
  const [sim, setSim] = useState<SimulationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState("metropolis");
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    api.scenarios().then((s) => { setScenarios(s); if (s.length && !s.some((x) => x.id === scenario)) setScenario(s[0].id); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // ?replay=<scenario> runs the replay on load and scrolls to it (handy for demo links).
    const auto = new URLSearchParams(window.location.search).get("replay");
    if (auto) {
      setScenario(auto);
      run(auto).then(() => document.getElementById("sim-h")?.scrollIntoView());
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(id: string = scenario) {
    setBusy(true);
    try {
      const r = await api.simulate(id);
      setSim(r);
      setStep(r.calls.length);
      setPlaying(false);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // Replay: advance one call every 700 ms.
  useEffect(() => {
    if (!playing || !sim) return;
    if (step >= sim.calls.length) { setPlaying(false); return; }
    const t = setTimeout(() => setStep((s) => s + 1), 700);
    return () => clearTimeout(t);
  }, [playing, step, sim]);

  const strategies = useMemo(
    () => (sim ? ORDER.map((id) => sim.strategies.find((s) => s.id === id)).filter((s): s is SimStrategy => !!s) : []),
    [sim],
  );
  const byId = useMemo(() => new Map(strategies.map((s) => [s.id, s])), [strategies]);
  const hosp = useMemo(() => new Map((sim?.hospitals ?? []).map((h) => [h.id, h])), [sim]);
  const nameOf = (id: string | null) => (id ? hosp.get(id)?.short_name ?? id : "–");

  const base = byId.get(BASELINE);
  const metro = byId.get("metropolis");
  const shown = byId.get(view);
  const maxMean = Math.max(1, ...strategies.map((s) => s.metrics.mean));
  const calls = sim?.calls ?? [];
  const current = step > 0 ? calls[step - 1] : null;
  const load = step > 0 && shown ? shown.timeline[step - 1] ?? {} : {};
  const assignOf = (s: SimStrategy | undefined, callId: number) => s?.assignments.find((a) => a.call_id === callId);

  return (
    <section className="panel" aria-labelledby="sim-h">
      <div className="panel-head">
        <h2 id="sim-h">Before vs. after: surge replay</h2>
        <span className="sample">Simulation · fictional hospitals · modeled wait times</span>
      </div>
      <div className="panel-body">
        <p className="rule">
          The same calls, run through how destinations are chosen today and through Metropolis. Every strategy is
          measured the same way at arrival: time on the road + ED offload wait (including ambulances queued ahead)
          {sim ? ` + ${sim.secondary_transfer_min} min transfer if the hospital can't treat the patient` : ""}.
        </p>
        <div className="row">
          <label className="field">
            <span className="label">Scenario</span>
            <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
              {scenarios.map((s) => <option key={s.id} value={s.id}>{s.title} ({s.calls} calls)</option>)}
            </select>
          </label>
          <button className="btn primary" onClick={() => run()} disabled={busy}>{busy ? "Running…" : sim ? "Run again" : "Run replay"}</button>
        </div>
        {error && <div className="error" role="alert">{error}</div>}

        <div aria-live="polite">
          {sim && base && metro && (
            <>
              <p className="sim-headline">
                <b>Metropolis: {delta(base.metrics.mean, metro.metrics.mean)} per patient</b> than the nearest capable hospital
                ({base.metrics.total - metro.metrics.total} patient-minutes across {sim.calls.length} calls), and critical patients{" "}
                {delta(base.metrics.acuity1_mean ?? 0, metro.metrics.acuity1_mean ?? 0)}.
              </p>
              <p className="rule">{sim.description}</p>
              <div className="sim-cards">
                {strategies.map((s) => <StrategyCard key={s.id} s={s} base={base} maxMean={maxMean} nameOf={nameOf} />)}
              </div>
            </>
          )}
        </div>

        {sim && shown && (
          <>
            <div className="row sim-controls">
              <fieldset>
                <legend className="label">Replay</legend>
                <div className="seg">
                  {strategies.map((s) => (
                    <label key={s.id}>
                      <input type="radio" name="sim-view" checked={view === s.id} onChange={() => setView(s.id)} />
                      <span>{s.label}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <button className="btn" onClick={() => { if (step >= calls.length) setStep(0); setPlaying((p) => !p); }}>
                {playing ? "Pause" : step >= calls.length ? "Replay" : "Play"}
              </button>
              <label className="field">
                <span className="label">Call {step} of {calls.length}</span>
                <input
                  type="range" min={0} max={calls.length} value={step}
                  onChange={(e) => { setPlaying(false); setStep(+e.target.value); }}
                  aria-valuetext={current ? `Call ${step}: ${current.label}, ${CATEGORY[current.category] ?? current.category}` : "Start"}
                />
              </label>
            </div>
            <p className="rule" aria-live="polite">
              {current
                ? <>t+{current.t_min} min · <b>{current.label}</b> · {CATEGORY[current.category] ?? current.category}, acuity {current.acuity} → <b>{nameOf(assignOf(shown, current.id)?.hospital_id ?? null)}</b>, {assignOf(shown, current.id)?.minutes_to_care} min to care</>
                : "Press Play to replay the calls one by one."}
            </p>
            <div className="map sim-map">
              <MapContainer center={[42.355, -71.09]} zoom={12} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
                <TileLayer
                  className="basemap"
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                {calls.slice(0, step).map((c, i) => {
                  const a = assignOf(shown, c.id);
                  const h = a && hosp.get(a.hospital_id);
                  if (!a || !h) return null;
                  const latest = i === step - 1;
                  return (
                    <Polyline
                      key={`l-${view}-${c.id}`}
                      positions={[[c.lat, c.lon], [h.lat, h.lon]]}
                      pathOptions={{
                        color: !a.capable ? CSS("--critical") : a.swapped ? CSS("--good") : CSS("--accent"),
                        weight: latest ? 4 : 2, opacity: latest ? 1 : 0.55, dashArray: a.capable ? undefined : "6 6",
                      }}
                    />
                  );
                })}
                {calls.slice(0, step).map((c, i) => (
                  <CircleMarker
                    key={`c-${c.id}`}
                    center={[c.lat, c.lon]}
                    radius={i === step - 1 ? 7 : 4}
                    pathOptions={{ color: "#ffffff", weight: 2, fillColor: c.acuity === 1 ? CSS("--critical") : CSS("--delay"), fillOpacity: 1 }}
                  >
                    <Tooltip direction="top" offset={[0, -6]}>#{c.id} {c.label} · {CATEGORY[c.category] ?? c.category} · acuity {c.acuity}</Tooltip>
                  </CircleMarker>
                ))}
                {(sim.hospitals).map((h) => {
                  const n = load[h.id] ?? 0;
                  return (
                    <CircleMarker
                      key={`h-${h.id}`}
                      center={[h.lat, h.lon]}
                      radius={6 + n * 2.5}
                      pathOptions={{ color: n >= 5 ? CSS("--critical") : CSS("--accent"), weight: 2, fillColor: n >= 5 ? CSS("--critical-soft") : CSS("--accent-soft"), fillOpacity: 0.9 }}
                    >
                      <Tooltip direction="right" offset={[8, 0]} permanent={n > 0}>{h.short_name}{n > 0 ? ` · ${n} 🚑` : ""}</Tooltip>
                    </CircleMarker>
                  );
                })}
              </MapContainer>
            </div>
            <p className="rule">Circle size = ambulances on the way to or waiting at that ED. Red dashed lines: hospital can't treat the patient. Green lines: route changed by a swap.</p>

            <div className="table-scroll">
              <table>
                <caption className="label sim-caption">Per call: today vs. Metropolis</caption>
                <thead>
                  <tr><th>#</th><th>Time</th><th>Location</th><th>Patient</th><th>Nearest capable (today)</th><th>Metropolis</th><th>Difference</th></tr>
                </thead>
                <tbody>
                  {calls.map((c) => {
                    const b = assignOf(base, c.id), m = assignOf(metro, c.id);
                    const d = b && m ? b.minutes_to_care - m.minutes_to_care : 0;
                    return (
                      <tr key={c.id}>
                        <td className="mono">{c.id}</td>
                        <td className="mono">t+{c.t_min}</td>
                        <td>{c.label}</td>
                        <td>{CATEGORY[c.category] ?? c.category} · acuity {c.acuity}</td>
                        <td>{b ? `${nameOf(b.hospital_id)} · ${b.minutes_to_care} min` : "–"}</td>
                        <td>{m ? `${nameOf(m.hospital_id)} · ${m.minutes_to_care} min` : "–"}</td>
                        <td className={`mono ${d > 0 ? "gain" : d < 0 ? "loss" : ""}`}>{d > 0 ? `−${d}` : d < 0 ? `+${-d}` : "0"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
