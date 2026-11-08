import { useCallback, useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, type AdminEvent, type Assignment, type Person, type RoleChange, type RoleView, type ToolInfo } from "../lib/api";
import "./Console.css";
import "./People.css";

// Admin view (Cognito group "admin" only, and the server checks it again on every call): who has which office
// role, what each role's assistant may do, and a log of every change. Saving changes DynamoDB; Cedar enforces it
// on the assistant's next tool call, so this page never decides anything by itself.

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Turn one tool on or off in a role's list. */
export function toggleTool(tools: string[], name: string): string[] {
  return tools.includes(name) ? tools.filter((t) => t !== name) : [...tools, name];
}

export function ChangeWarning({ change }: { change: RoleChange | null }) {
  if (!change) return null;
  if (change.changes.length === 0) return <p className="muted">No change yet. Tick or untick a tool to see what it allows.</p>;
  const who = change.affects.length
    ? `This applies to ${change.affects.join(", ")} on their assistant's very next action, even mid-conversation.`
    : "Nobody has this role right now; it applies to anyone given it later.";
  return (
    <div className="role-warning" role="status" aria-live="polite">
      <strong>What this change does</strong>
      <ul>
        {change.changes.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <p>{who}</p>
    </div>
  );
}

interface RoleEditorFieldsProps {
  role: RoleView;
  tools: ToolInfo[];
  draft: string[];
  busy: boolean;
  onPick: (name: string) => void;
}

/** One checkbox per tool. A tool the server would refuse for this role (blocked for everyone, or locked by policy
 *  to other roles) is greyed out; the server checks again whatever the browser sends. */
export function RoleEditorFields({ role, tools, draft, busy, onPick }: RoleEditorFieldsProps) {
  return (
    <fieldset>
      <legend>Their assistant may</legend>
      {tools.map((t) => {
        const blocked = t.alwaysDenied || !role.assignable.includes(t.name);
        return (
          <label key={t.name} className={blocked ? "tool-check blocked" : "tool-check"}>
            <input type="checkbox" checked={!blocked && draft.includes(t.name)} disabled={blocked || busy} onChange={() => onPick(t.name)} />
            <span>
              {t.action}
              {t.alwaysDenied
                ? " (blocked for everyone)"
                : !role.assignable.includes(t.name)
                  ? ` (locked by policy to the ${t.lockedTo.join(" and ")})`
                  : ""}
            </span>
          </label>
        );
      })}
    </fieldset>
  );
}

interface RoleCardProps {
  role: RoleView;
  tools: ToolInfo[];
  onSaved: () => void;
}

export function RoleCard({ role, tools, onSaved }: RoleCardProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string[]>(role.tools);
  const [change, setChange] = useState<RoleChange | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const action = (name: string) => tools.find((t) => t.name === name)?.action ?? name;

  async function pick(name: string) {
    const next = toggleTool(draft, name);
    setDraft(next);
    setError(null);
    try {
      setChange(await api.previewRole(role.id, next)); // the server's words, so the warning matches what it enforces
    } catch (e) {
      setChange(null);
      setError(errorText(e));
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.setRoleTools(role.id, draft);
      setEditing(false);
      setChange(null);
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setEditing(false);
    setDraft(role.tools);
    setChange(null);
    setError(null);
  }

  return (
    <li className="role-card">
      <div className="role-head">
        <h3>{role.label}</h3>
        <span className="muted">
          {role.people.length} {role.people.length === 1 ? "person" : "people"}
          {role.customized && role.updatedBy ? ` · changed by ${role.updatedBy}` : ""}
        </span>
      </div>
      {editing ? (
        <div className="role-editor">
          <RoleEditorFields role={role} tools={tools} draft={draft} busy={busy} onPick={pick} />
          <ChangeWarning change={change} />
          {error && (
            <p className="alert alert-error" role="alert">
              {error}
            </p>
          )}
          <div className="row-actions">
            <button type="button" onClick={save} disabled={busy || !change || change.changes.length === 0}>
              {busy ? "Saving…" : "Save this change"}
            </button>
            <button type="button" className="btn-plain" onClick={cancel} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <ul className="role-tools" aria-label={`What a ${role.label}'s assistant may do`}>
            {role.tools.length ? role.tools.map((t) => <li key={t}>{action(t)}</li>) : <li className="muted">Nothing</li>}
          </ul>
          <button type="button" className="btn-plain" onClick={() => setEditing(true)}>
            Change what this role may do
          </button>
        </>
      )}
    </li>
  );
}

interface PersonRowProps {
  person: Person;
  roles: RoleView[];
  onSaved: () => void;
}

export function PersonRow({ person, roles, onSaved }: PersonRowProps) {
  const [officeRole, setOfficeRole] = useState(person.officeRole ?? "");
  const [group, setGroup] = useState(person.group ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const change: Assignment = {
    ...(officeRole && officeRole !== person.officeRole ? { officeRole } : {}),
    ...(group && group !== person.group ? { group: group as Assignment["group"] } : {}),
  };
  const changed = Object.keys(change).length > 0;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.assign(person.username, change);
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="person">
      <div className="person-who">
        <strong>{person.username}</strong>
        <span className="muted">
          {person.email ? ` · ${person.email}` : ""}
          {!person.inDirectory ? " · no login" : !person.enabled ? " · login disabled" : ""}
          {person.officeRole && !person.active ? " · offboarded" : ""}
        </span>
      </div>
      <label className="person-field">
        <span>Office role</span>
        <select value={officeRole} onChange={(e) => setOfficeRole(e.target.value)} disabled={busy}>
          {!person.officeRole && <option value="">No role</option>}
          {roles.map((r) => (
            <option key={r.id} value={r.id}>
              {r.label}
            </option>
          ))}
        </select>
      </label>
      <label className="person-field">
        <span>Access</span>
        <select value={group} onChange={(e) => setGroup(e.target.value)} disabled={busy || !person.inDirectory}>
          {!person.group && <option value="">No group</option>}
          <option value="staffer">Staffer</option>
          <option value="admin">Admin (this page)</option>
        </select>
      </label>
      <div className="person-save">
        {changed && (
          <p className="muted">
            {change.officeRole ? "Their assistant's next action uses the new role. " : ""}
            {change.group === "admin" ? "They will be able to change everyone's roles." : ""}
            {change.group === "staffer" ? "They lose this page right away." : ""}
          </p>
        )}
        <button type="button" onClick={save} disabled={!changed || busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        {error && (
          <p className="alert alert-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </li>
  );
}

export function ChangeLog({ events }: { events: AdminEvent[] }) {
  if (!events.length) return <p className="muted">No changes yet.</p>;
  return (
    <ul className="change-log">
      {events.map((e) => (
        <li key={e.id}>
          <span className="muted">{new Date(e.ts).toLocaleString([], { hour12: false })}</span> <strong>{e.actor}</strong>: {e.summary}
        </li>
      ))}
    </ul>
  );
}

interface Data {
  people: Person[];
  roles: RoleView[];
  tools: ToolInfo[];
  events: AdminEvent[];
}

export default function People() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, r, a] = await Promise.all([api.people(), api.roles(), api.adminAudit()]);
      setData({ people: p.people, roles: r.roles, tools: r.tools, events: a.events });
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Layout>
      <div className="mandate-console people-page">
        <header className="title-row">
          <div>
            <h1>People &amp; roles</h1>
            <p className="lede">
              Each person has one office role. The role decides what their assistant may do, and Verified Permissions
              enforces it on every action, so a change here applies on the assistant&apos;s next action.
            </p>
          </div>
        </header>

        {error && (
          <p className="alert alert-error" role="alert">
            {error}
          </p>
        )}
        {!data && !error && <p className="muted">Loading…</p>}

        {data && (
          <>
            <section className="panel" aria-labelledby="roles-title">
              <div className="panel-head">
                <h2 id="roles-title">Office roles</h2>
              </div>
              <ul className="role-grid">
                {data.roles.map((r) => (
                  // keyed by the saved list so the card resets after a save
                  <RoleCard key={`${r.id}:${r.tools.join(",")}`} role={r} tools={data.tools} onSaved={load} />
                ))}
              </ul>
            </section>

            <section className="panel" aria-labelledby="people-title">
              <div className="panel-head">
                <h2 id="people-title">People</h2>
              </div>
              <ul className="people">
                {data.people.map((p) => (
                  <PersonRow key={`${p.username}:${p.officeRole}:${p.group}`} person={p} roles={data.roles} onSaved={load} />
                ))}
              </ul>
            </section>

            <section className="panel" aria-labelledby="log-title">
              <div className="panel-head">
                <h2 id="log-title">Changes</h2>
              </div>
              <ChangeLog events={data.events} />
            </section>
          </>
        )}
      </div>
    </Layout>
  );
}
