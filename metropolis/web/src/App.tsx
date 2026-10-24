import { useCallback, useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMapEvents } from "react-leaflet";
import { api, type Category, type DemoState, type Dispatch, type Hospital, type Recommendation, type ScoreResponse, type SwapSide, type SwapSuggestion, type Traffic } from "./api";
import Simulation from "./Simulation";

interface Place { label: string; lat: number; lon: number }

const PRESETS: Place[] = [
  { label: "Kenmore Sq, Boston", lat: 42.3489, lon: -71.0952 },
  { label: "Fields Corner, Dorchester", lat: 42.3002, lon: -71.0617 },
  { label: "Davis Sq, Somerville", lat: 42.3967, lon: -71.1224 },
  { label: "Chelsea Sq, Chelsea", lat: 42.3918, lon: -71.0328 },
  { label: "Quincy Center, Quincy", lat: 42.2510, lon: -71.0040 },
  { label: "Newton Centre, Newton", lat: 42.3295, lon: -71.1925 },
];
const TRAFFIC: { id: Traffic; label: string }[] = [
  { id: "light", label: "Light" },
  { id: "normal", label: "Normal" },
  { id: "rush", label: "Rush hour" },
];
const ACUITY: { id: number; label: string }[] = [
  { id: 1, label: "1 · Critical" },
  { id: 2, label: "2 · Emergent" },
  { id: 3, label: "3 · Urgent" },
  { id: 4, label: "4 · Non-urgent" },
];
const CSS = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function MapClicks({ onPick }: { onPick: (p: Place) => void }) {
  useMapEvents({ click: (e) => onPick({ label: "Point picked on map", lat: e.latlng.lat, lon: e.latlng.lng }) });
  return null;
}

function OffloadChip({ r }: { r: Recommendation }) {
  const o = r.offload;
  if (o.source === "live_feed") return <span className="chip ok">Live ED feed · {Math.round(o.data_age_min ?? 0)} min old</span>;
  if (o.stale) return <span className="chip warn">Feed stale ({Math.round(o.data_age_min ?? 0)} min) · historical delay</span>;
  return <span className="chip warn">No live feed · historical delay</span>;
}

/** "Without Metropolis" vs "Metropolis" for the current call. */
function ComparisonStrip({ result }: { result: ScoreResponse }) {
  const base = result.comparison.nearest_capable;
  const ed = result.comparison.nearest_ed;
  const top = result.recommendations[0];
  if (!base || !top) return null;
  const saved = base.minutes_total - top.minutes_total;
  return (
    <div className="compare" aria-label="Comparison with the nearest capable hospital">
      {base.same_as_top ? (
        <p><b>Metropolis agrees</b> with the nearest capable hospital, {base.short_name}: {top.minutes_total} min to care.</p>
      ) : (
        <div className="compare-grid">
          <div>
            <span className="label">Today · nearest capable</span>
            <div className="compare-pick">{base.short_name} · <b>{base.minutes_total} min</b></div>
            <small>{base.minutes_drive} drive + {base.minutes_offload} ED wait</small>
          </div>
          <div>
            <span className="label">Metropolis</span>
            <div className="compare-pick">{top.short_name} · <b>{top.minutes_total} min</b></div>
            <small>{top.minutes_drive} drive + {top.minutes_offload} ED wait</small>
          </div>
          <div className={`compare-saved${saved > 0 ? " pos" : ""}`}>
            <span className="label">Difference</span>
            <div className="compare-pick"><b>{saved > 0 ? `Saves ${saved} min` : saved < 0 ? `${-saved} min slower` : "Same time"}</b></div>
          </div>
        </div>
      )}
      {ed && !ed.capable && (
        <p className="compare-warn">Closest ED, {ed.short_name} ({ed.minutes_drive} min drive), can't treat this patient: {ed.reason}.</p>
      )}
    </div>
  );
}

function SwapSideRow({ s, nameOf }: { s: SwapSide; nameOf: (id: string) => string }) {
  const delta = s.minutes_after - s.minutes_before;
  return (
    <li>
      <span>
        <b>{s.unit}</b> · acuity {s.acuity} · {nameOf(s.from_hospital)} → <b>{nameOf(s.to_hospital)}</b>
      </span>
      <span className={delta < 0 ? "gain" : delta > 0 ? "loss" : ""}>
        {s.minutes_before} → {s.minutes_after} min ({delta > 0 ? "+" : ""}{delta})
      </span>
    </li>
  );
}

export default function App() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [rulesVersion, setRulesVersion] = useState("");
  const [category, setCategory] = useState("trauma");
  const [place, setPlace] = useState<Place>(PRESETS[0]);
  const [traffic, setTraffic] = useState<Traffic>("normal");
  const [result, setResult] = useState<ScoreResponse | null>(null);
  const [hospitals, setHospitals] = useState<Hospital[]>([]);
  const [log, setLog] = useState<Dispatch[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [unitNo, setUnitNo] = useState(1);
  const [acuity, setAcuity] = useState<number | null>(null); // null = category default
  const [swaps, setSwaps] = useState<SwapSuggestion[]>([]);
  const [demo, setDemo] = useState<DemoState | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, hs, ds, sw] = await Promise.all([
        api.score({ category, lat: place.lat, lon: place.lon, traffic }),
        api.hospitals(),
        api.dispatches(),
        api.evaluateSwaps({ traffic }),
      ]);
      setResult(s);
      setHospitals(hs);
      setLog(ds);
      setSwaps(sw);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [category, place, traffic]);

  useEffect(() => {
    api.rules()
      .then((r) => { setCategories(r.categories); setRulesVersion(r.version); })
      .catch((e) => setError(`Can't reach the API. Is it running on port 8000? (${e.message})`));
    api.demo().then(setDemo).catch(() => setDemo(null));
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  // Feed jobs update hospital status in the background; poll so the screen follows.
  useEffect(() => { const t = setInterval(refresh, 30_000); return () => clearInterval(t); }, [refresh]);

  const rankOf = useMemo(
    () => new Map((result?.recommendations ?? []).map((r) => [r.hospital_id, r.rank])),
    [result],
  );
  const eligible = useMemo(
    () => new Set([...(result?.recommendations ?? []), ...(result?.ranked_lower ?? [])].map((r) => r.hospital_id)),
    [result],
  );
  const cat = categories.find((c) => c.id === category);
  const patientAcuity = acuity ?? cat?.default_acuity ?? 3;
  const top = result?.recommendations ?? [];
  const scale = Math.max(45, ...top.map((r) => r.minutes_total));

  async function send(r: Recommendation) {
    if (!result) return;
    const unit = `Medic ${unitNo}`;
    try {
      await api.dispatch({
        unit, hospital_id: r.hospital_id, category, recommended_rank: r.rank, score_audit_id: result.audit_id,
        acuity: patientAcuity, origin_lat: place.lat, origin_lon: place.lon,
      });
      setUnitNo((n) => n + 1);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function demoAction(load: boolean) {
    if (load && !window.confirm("Load the demo scenario? This deletes all dispatches and swap suggestions and freezes the simulated hospital feed.")) return;
    try {
      setDemo(load ? await api.demoLoad("surge") : await api.demoUnfreeze());
      setUnitNo(1);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function decide(s: SwapSuggestion, accept: boolean) {
    try {
      await (accept ? api.acceptSwap(s.id) : api.declineSwap(s.id));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      await refresh();
    }
  }

  async function setStatus(h: Hospital, body: Parameters<typeof api.updateStatus>[1]) {
    try { await api.updateStatus(h.id, body); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  const nameOf = (id: string) => hospitals.find((h) => h.id === id)?.short_name ?? id;

  return (
    <div className="wrap">
      <header className="top">
        <div>
          <h1>Metropolis</h1>
          <p className="sub">Filters hospitals by what they can treat, then ranks them by minutes to definitive care: drive time plus predicted ED offload delay.</p>
        </div>
        <div className="sample">Sample data · fictional hospitals · not for clinical use</div>
      </header>

      {error && <div className="error" role="alert">{error}</div>}

      {demo?.enabled && (
        <div className="demo-bar" role="region" aria-label="Demo mode">
          <span className="label">Demo mode</span>
          <span className="grow" aria-live="polite">
            {demo.frozen ? `Hospital feeds frozen at scenario "${demo.scenario}".` : "Simulated hospital feeds are changing every minute."}
          </span>
          <button className="btn" onClick={() => demoAction(true)}>{demo.frozen ? "Reload scenario" : "Load demo scenario"}</button>
          {demo.frozen && <button className="btn" onClick={() => demoAction(false)}>Unfreeze feeds</button>}
        </div>
      )}

      <div className="grid">
        <section className="panel">
          <div className="panel-head"><h2>Incident</h2><span className="label">{place.label}</span></div>
          <div className="panel-body">
            <fieldset>
              <legend className="label">Incident category</legend>
              <div className="pills">
                {categories.map((c) => (
                  <label className="pill" key={c.id}>
                    <input type="radio" name="cat" value={c.id} checked={c.id === category} onChange={() => { setCategory(c.id); setAcuity(null); }} />
                    <span>{c.label} <small>{c.rule_id}</small></span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="row">
              <label className="field">
                <span className="label">Location</span>
                <select
                  value={PRESETS.findIndex((p) => p.label === place.label)}
                  onChange={(e) => setPlace(PRESETS[+e.target.value])}
                >
                  {PRESETS.map((p, i) => <option key={p.label} value={i}>{p.label}</option>)}
                  <option value={-1} disabled>Point picked on map</option>
                </select>
              </label>
              <fieldset>
                <legend className="label">Traffic</legend>
                <div className="seg">
                  {TRAFFIC.map((t) => (
                    <label key={t.id}>
                      <input type="radio" name="traffic" checked={traffic === t.id} onChange={() => setTraffic(t.id)} />
                      <span>{t.label}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <label className="field narrow">
                <span className="label">Patient acuity</span>
                <select value={patientAcuity} onChange={(e) => setAcuity(+e.target.value)}>
                  {ACUITY.map((a) => (
                    <option key={a.id} value={a.id}>{a.label}{a.id === cat?.default_acuity ? " (default)" : ""}</option>
                  ))}
                </select>
              </label>
            </div>
          </div>
          <div className="map">
            <MapContainer center={[42.355, -71.09]} zoom={12} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
              {/* Standard OSM tiles, muted with CSS (.basemap) so markers and routes stand out. */}
              <TileLayer
                className="basemap"
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              <MapClicks onPick={setPlace} />
              {top.slice().reverse().map((r) => (
                <Polyline
                  key={`route-${r.hospital_id}`}
                  positions={[[place.lat, place.lon], [r.lat, r.lon]]}
                  pathOptions={r.rank === 1 ? { color: CSS("--accent"), weight: 4 } : { color: CSS("--accent"), weight: 2, dashArray: "6 6", opacity: 0.6 }}
                />
              ))}
              {hospitals.map((h) => {
                const rank = rankOf.get(h.id);
                const ok = eligible.has(h.id);
                return (
                  <CircleMarker
                    key={h.id}
                    center={[h.lat, h.lon]}
                    radius={rank ? 11 : 7}
                    pathOptions={{
                      color: h.on_diversion ? CSS("--critical") : ok ? CSS("--accent") : CSS("--muted"),
                      fillColor: rank ? CSS("--accent") : ok ? "#ffffff" : CSS("--excluded"),
                      fillOpacity: 1,
                      weight: h.on_diversion ? 3 : 2,
                    }}
                  >
                    <Tooltip direction="right" offset={[10, 0]} permanent={!!rank}>
                      {rank ? `${rank}. ` : ""}{h.short_name}
                    </Tooltip>
                  </CircleMarker>
                );
              })}
              <CircleMarker center={[place.lat, place.lon]} radius={9} pathOptions={{ color: "#ffffff", weight: 3, fillColor: CSS("--critical"), fillOpacity: 1 }}>
                <Tooltip direction="top" offset={[0, -8]}>Incident</Tooltip>
              </CircleMarker>
              {log.filter((d) => !d.arrived_at && d.position_lat != null && d.position_lon != null).map((d) => (
                <CircleMarker
                  key={`unit-${d.id}`}
                  center={[d.position_lat!, d.position_lon!]}
                  radius={6}
                  pathOptions={{ color: CSS("--ink"), weight: 2, fillColor: CSS("--delay"), fillOpacity: 1 }}
                >
                  <Tooltip direction="top" offset={[0, -6]}>{d.unit} → {nameOf(d.hospital_id)}</Tooltip>
                </CircleMarker>
              ))}
            </MapContainer>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head"><h2>Recommended destinations</h2><span className="label">Rules {rulesVersion}</span></div>
          <div className="panel-body">
            {result && cat && (
              <p className="rule">
                Rule <code>{result.rule_id}</code>: <b>{cat.label}</b> goes to a {result.need}. Routing: {result.routing_source === "osrm" ? "OSRM road network" : "straight-line estimate"}.
              </p>
            )}
            {result && <ComparisonStrip result={result} />}
            <div className="recs">
              {result && top.length === 0 && (
                <div className="rec first"><b>No hospital meets rule {result.rule_id}.</b> Protocol fallback: nearest open ED, contact medical control.</div>
              )}
              {top.map((r) => (
                <article key={r.hospital_id} className={`rec${r.rank === 1 ? " first" : ""}`}>
                  <div className="rank">{r.rank}</div>
                  <div className="rec-name">{r.name}</div>
                  <div className="rec-min">{r.minutes_total}<small>MIN TO CARE</small></div>
                  <div className="rec-main">
                    <div className="bar" aria-hidden="true">
                      <i className="d" style={{ width: `${(r.minutes_drive / scale) * 100}%` }} />
                      <i className="w" style={{ width: `${(r.minutes_offload / scale) * 100}%` }} />
                    </div>
                    <div className="split"><span className="sd">Drive {r.minutes_drive} min</span><span className="sw">ED offload {r.minutes_offload} min</span></div>
                    <div className="chips">
                      <span className="chip ok">{r.capability} · <code>{r.rule_id}</code></span>
                      <OffloadChip r={r} />
                      {r.units_en_route > 0 && <span className="chip">{r.units_en_route} unit{r.units_en_route > 1 ? "s" : ""} en route</span>}
                      {r.diversion_exempt && <span className="chip crit">On diversion · exempt · <code>D-1</code></span>}
                    </div>
                    <div className="actions">
                      <button className={`btn${r.rank === 1 ? " primary" : ""}`} onClick={() => send(r)}>Send Medic {unitNo} here · acuity {patientAcuity}</button>
                    </div>
                  </div>
                </article>
              ))}
            </div>

            {result && (result.ranked_lower.length > 0 || result.excluded.length > 0) && (
              <div className="why">
                <span className="label">Not recommended</span>
                <ul>
                  {result.ranked_lower.map((r) => <li key={r.hospital_id}><span>{r.rank}. {r.short_name}</span><span>Ranked lower · {r.minutes_total} min</span></li>)}
                  {result.excluded.map((x) => <li key={x.hospital_id}><span>{x.short_name}</span><span>{x.reason} · {x.rule_id}</span></li>)}
                </ul>
              </div>
            )}

            <div className="why">
              <span className="label">Dispatch log</span>
              {log.length === 0 && <span className="empty">No units sent yet.</span>}
              <ul>
                {log.map((d) => (
                  <li key={d.id}>
                    <span>
                      {new Date(d.dispatched_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · {d.unit} (acuity {d.acuity}) → {nameOf(d.hospital_id)}
                      {d.recommended_rank && d.recommended_rank > 1 ? ` (crew chose #${d.recommended_rank})` : ""}
                      {d.last_swapped_at ? " · swapped" : ""}
                    </span>
                    <span>
                      {d.arrived_at ? "Arrived" : <button className="ghost" onClick={async () => { await api.arrive(d.id); await refresh(); }}>Mark arrived</button>}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      </div>

      <section className="panel" aria-labelledby="swaps-h">
        <div className="panel-head">
          <h2 id="swaps-h">Suggested swaps</h2>
          <span className="label">Ranker · rule R-1 · suggestions only</span>
        </div>
        <div className="panel-body">
          <p className="rule">
            Trades destinations between two units in transport when it lowers severity-weighted minutes to care.
            Both hospitals must still meet each patient's rule. A dispatcher must accept every swap.
          </p>
          {swaps.length === 0 && <span className="empty">No swaps would help right now.</span>}
          <div className="recs" aria-live="polite">
            {swaps.map((s) => {
              const estimated = [s.a, s.b].filter((x) => x.position_source === "incident_origin").map((x) => x.unit);
              const staleOff = [s.a, s.b].filter((x) => x.offload.source !== "live_feed").map((x) => nameOf(x.to_hospital));
              return (
                <article key={s.id} className="rec swap" aria-label={`Swap ${s.a.unit} and ${s.b.unit}`}>
                  <div className="rank" aria-hidden="true">⇄</div>
                  <div className="rec-name">{s.a.unit} ⇄ {s.b.unit}</div>
                  <div className="rec-min">{s.weighted_gain}<small>WEIGHTED MIN SAVED</small></div>
                  <div className="rec-main">
                    <div className="why compact">
                      <ul>
                        <SwapSideRow s={s.a} nameOf={nameOf} />
                        <SwapSideRow s={s.b} nameOf={nameOf} />
                      </ul>
                    </div>
                    <div className="chips">
                      {s.pareto
                        ? <span className="chip ok">Both patients faster</span>
                        : <span className="chip">Trade-off favors higher acuity</span>}
                      <span className="chip"><code>{s.rule_id}</code> · rules {s.rules_version}</span>
                      {s.routing_source !== "osrm" && <span className="chip warn">Straight-line drive estimate</span>}
                      {estimated.length > 0 && <span className="chip warn">Position estimated from incident: {estimated.join(", ")}</span>}
                      {staleOff.length > 0 && <span className="chip warn">Historical delay at {[...new Set(staleOff)].join(", ")}</span>}
                    </div>
                    <div className="actions gap">
                      <button className="btn" onClick={() => decide(s, false)}>Decline</button>
                      <button className="btn primary" onClick={() => decide(s, true)}>Accept swap</button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      <Simulation />

      <section className="panel">
        <div className="panel-head"><h2>Hospital status board</h2><span className="label">Changes are saved to the API</span></div>
        <div className="panel-body table-scroll">
          <table>
            <thead>
              <tr><th>Hospital</th><th>Capabilities</th><th>Data feed</th><th>ED occupancy %</th><th>Boarding</th><th>En route</th><th>ED diversion</th><th>Predicted offload</th></tr>
            </thead>
            <tbody>
              {hospitals.map((h) => (
                <tr key={h.id}>
                  <td className="name">{h.name}</td>
                  <td><div className="caps">{h.capabilities.map((c) => <span className="cap" key={c}>{c}</span>)}</div></td>
                  <td>
                    <select value={h.feed} onChange={(e) => setStatus(h, { feed: e.target.value as Hospital["feed"] })}>
                      <option value="live">Live feed</option>
                      <option value="none">No live feed</option>
                    </select>
                  </td>
                  <td>
                    <input
                      className="num" type="number" min={0} max={300} disabled={h.feed !== "live"}
                      defaultValue={h.ed_occupancy_pct ?? ""} key={`occ-${h.id}-${h.status_updated_at}`}
                      onBlur={(e) => e.target.value !== "" && setStatus(h, { ed_occupancy_pct: +e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      className="num" type="number" min={0} max={200} disabled={h.feed !== "live"}
                      defaultValue={h.boarding ?? ""} key={`board-${h.id}-${h.status_updated_at}`}
                      onBlur={(e) => e.target.value !== "" && setStatus(h, { boarding: +e.target.value })}
                    />
                  </td>
                  <td className="mono">{h.units_en_route}</td>
                  <td>
                    <label className="switch">
                      <input type="checkbox" checked={h.on_diversion} onChange={(e) => setStatus(h, { on_diversion: e.target.checked })} />
                      <span className="t" /><span className="l">{h.on_diversion ? "On diversion" : "Open"}</span>
                    </label>
                  </td>
                  <td className="mono">
                    {h.predicted_offload.minutes} min
                    <small>{h.predicted_offload.source === "live_feed" ? "live" : h.predicted_offload.stale ? "stale feed, historical" : "historical median"}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
