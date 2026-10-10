/** Client profile: the facts that decide which statutory limits apply to a client. */

import { useEffect, useState } from "react";
import { saveProfile, useProfile, type Row } from "../api";
import { ActionButton, Caption, Grid, Loaded, Notice, Select, TextInput } from "../ui";

const CHOICES: Record<string, [string, [string, string][]]> = {
  entity_type: ["Entity type", [["Individual", "individual"], ["HUF", "huf"], ["Partnership firm", "firm"], ["LLP", "llp"], ["Company", "company"], ["Other (AOP, trust …)", "other"]]],
  nature: ["Nature of work", [["Business", "business"], ["Profession", "profession"], ["Both", "both"]]],
  supplies: ["Supplies", [["Goods", "goods"], ["Services", "services"], ["Both", "both"]]],
  presumptive: ["Presumptive scheme opted", [["None", "none"], ["44AD (business)", "44AD"], ["44ADA (profession)", "44ADA"]]],
};
const BOOLS: Record<string, string> = {
  gst_registered: "GST registered",
  special_category: "Special-category state (GST)",
  cash_within_5pct: "Cash receipts & payments within 5%",
};
export const PROFILE_LABELS: Record<string, string> = {
  ...Object.fromEntries(Object.entries(CHOICES).map(([k, [label]]) => [k, label])),
  ...BOOLS,
};

/** Labels of profile fields still unknown (their limits keep being checked). */
export function useProfileGaps(clientId: number): string[] {
  const profile = useProfile(clientId);
  return (profile.data?.unknown_fields ?? []).map((name: string) => PROFILE_LABELS[name] ?? name);
}

export function ProfileForm({ client }: { client: Row }) {
  return <Loaded query={useProfile(client.id)}>{(profile) => <Form client={client} profile={profile} />}</Loaded>;
}

function Form({ client, profile }: { client: Row; profile: Row }) {
  const [values, setValues] = useState<Row>({});
  const [gstin, setGstin] = useState("");
  const [pan, setPan] = useState("");
  const [saved, setSaved] = useState<string[] | null>(null);
  useEffect(() => {
    setGstin(profile.gstin ?? "");
    setPan(profile.pan ?? "");
    setValues(Object.fromEntries([...Object.keys(CHOICES), ...Object.keys(BOOLS)].map((k) => [k, profile[k] ?? null])));
  }, [profile]);
  const set = (key: string) => (value: unknown) => setValues((v) => ({ ...v, [key]: value }));

  return (
    <>
      <Caption>
        These facts decide which statutory limits are checked for this client. A field left <b>Unknown</b> keeps its
        limits switched on, except LLP audit (needs Entity type = LLP) and 44ADA (needs Nature = Profession or the 44ADA
        scheme). GSTIN, PAN, state, registration and entity type are filled automatically from a Tally import.
      </Caption>
      <Grid cols={2}>
        <TextInput label="GSTIN" value={gstin} onChange={setGstin} maxLength={15} />
        <TextInput label="PAN" value={pan} onChange={setPan} maxLength={10} placeholder="from the GSTIN" />
      </Grid>
      {profile.state_name && (
        <Caption>
          State from GSTIN: {profile.state_name} ({profile.state_code})
        </Caption>
      )}
      {profile.entity_hint && <Notice>{profile.entity_hint}</Notice>}
      <Grid cols={2}>
        <div className="stack">
          {Object.entries(CHOICES).map(([name, [label, options]]) => (
            <Select
              key={name}
              label={label}
              value={values[name] ?? null}
              options={[[null, "Unknown"], ...options.map(([text, v]): [string, string] => [v, text])]}
              onChange={set(name)}
            />
          ))}
        </div>
        <div className="stack">
          {Object.entries(BOOLS).map(([name, label]) => (
            <Select
              key={name}
              label={label}
              value={values[name] ?? null}
              options={[[null, "Unknown"], [true, "Yes"], [false, "No"]]}
              onChange={set(name)}
            />
          ))}
        </div>
      </Grid>
      <ActionButton
        primary
        action={async () => {
          const result = await saveProfile(client.id, { gstin: gstin.trim() || null, pan: pan.trim().toUpperCase() || null, ...values });
          setSaved(result.unknown_fields);
        }}
      >
        Save profile and re-check alerts
      </ActionButton>
      {saved && (
        <>
          <Notice kind="success">
            Profile saved. Alerts for limits that no longer apply were acknowledged as 'System - not applicable to client
            profile'.
          </Notice>
          {saved.length > 0 && (
            <Caption>Still unknown (their limits stay on): {saved.map((u) => PROFILE_LABELS[u] ?? u).join(", ")}</Caption>
          )}
        </>
      )}
    </>
  );
}
