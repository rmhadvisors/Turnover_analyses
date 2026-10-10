/** Clients: the directory (add a client with its data, add data to a client, open one) and,
 * for the sidebar's client, its report, data coverage and management (rename / delete). */

import { useEffect, useRef, useState } from "react";
import { deleteClient, renameClient, useCoverage, useWorkspace, type Row } from "../api";
import ClientReport from "../components/ClientReport";
import { ClientCoverage, coverageMatrix } from "../components/Coverage";
import { ProfileForm, useProfileGaps } from "../components/Profile";
import { AddDataPanel } from "../components/TallyImport";
import { TdsReport } from "../components/tds/Report";
import { useSelection } from "../state";
import { ActionButton, Button, Caption, DataTable, Loaded, Modal, Notice, PageHeader, Section, Tabs, TextInput, autoColumns } from "../ui";

export default function ClientsPage() {
  const { clientId } = useSelection();
  const workspace = useWorkspace();
  const current = workspace.data?.clients.find((c: Row) => c.id === clientId);
  return current ? <ClientView client={current} /> : <Directory />;
}

function Directory() {
  const { setClientId } = useSelection();
  // "new": the Add client panel; a client: its Add data panel
  const [panel, setPanel] = useState<"new" | Row | null>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const workspace = useWorkspace();
  const coverage = useCoverage();
  const top = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (panel) top.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [panel]);
  return (
    <>
      <PageHeader title="Clients" subtitle="Add a client with its Tally data, add a year to a client, or open one." />
      <div ref={top} />
      {panel ? (
        <AddDataPanel
          key={panel === "new" ? "new" : panel.id}
          client={panel === "new" ? null : panel}
          onClose={() => setPanel(null)}
        />
      ) : (
        <Button primary onClick={() => setPanel("new")}>
          + Add client
        </Button>
      )}
      <Loaded query={workspace} text="Loading clients…">
        {(ws) => {
          const clients: Row[] = ws.clients;
          if (!clients.length) {
            return (
              <>
                <Section title="Client directory · 0" />
                <Notice>No clients yet. Add a client to begin importing data or entering figures.</Notice>
              </>
            );
          }
          const matrix = coverageMatrix(clients, coverage.data ?? []);
          const chosen = picked.length ? clients[picked[0]] : null;
          return (
            <>
              <Section title={`Client directory · ${clients.length}`} />
              <Caption>Select a row to open the client. Each year shows where its data came from.</Caption>
              <DataTable
                rows={matrix}
                columns={[
                  ...autoColumns(matrix),
                  {
                    key: "add",
                    label: "",
                    render: (r) => (
                      <button
                        className="btn btn-small"
                        disabled={!!panel}
                        title="Add a year of Tally data to this client"
                        onClick={(e) => {
                          e.stopPropagation();
                          setPanel(clients[matrix.indexOf(r)]);
                        }}
                      >
                        + Add data
                      </button>
                    ),
                  },
                ]}
                selection="single"
                selected={picked}
                onSelect={setPicked}
              />
              {chosen && (
                <Button primary onClick={() => setClientId(chosen.id)}>
                  Open {chosen.name} →
                </Button>
              )}
            </>
          );
        }}
      </Loaded>
    </>
  );
}

function RenameDialog({ client, onClose }: { client: Row; onClose: () => void }) {
  const [name, setName] = useState<string>(client.name);
  return (
    <Modal title="Rename client" onClose={onClose}>
      <TextInput label="Client name" value={name} onChange={setName} />
      <div>
        <ActionButton
          primary
          disabled={!name.trim()}
          action={async () => {
            await renameClient(client.id, name.trim());
            onClose();
          }}
        >
          Save name
        </ActionButton>
      </div>
    </Modal>
  );
}

function DeleteDialog({ client, onClose }: { client: Row; onClose: () => void }) {
  const { setClientId } = useSelection();
  const [confirmation, setConfirmation] = useState("");
  return (
    <Modal title="Delete client" onClose={onClose}>
      <Notice kind="error">Deleting this client permanently removes its vouchers, figures, alerts and import history.</Notice>
      <TextInput label={`Type ${client.name} to confirm deletion`} value={confirmation} onChange={setConfirmation} />
      <div>
        <ActionButton
          primary
          disabled={confirmation !== client.name}
          action={async () => {
            await deleteClient(client.id);
            setClientId(null);
            onClose();
          }}
        >
          Delete client
        </ActionButton>
      </div>
    </Modal>
  );
}

function ClientView({ client }: { client: Row }) {
  const { fy, setClientId } = useSelection();
  const gaps = useProfileGaps(client.id);
  const [dialog, setDialog] = useState<"rename" | "delete" | null>(null);
  const [adding, setAdding] = useState(false);
  return (
    <>
      <Button onClick={() => setClientId(null)}>← All clients</Button>
      <PageHeader title={client.name} subtitle="Client report, data coverage and client settings." />
      {gaps.length > 0 && (
        <Notice>
          Profile incomplete ({gaps.join(", ")}): limits that depend on these are still checked. Complete it on the Profile tab
          so only relevant alerts are raised.
        </Notice>
      )}
      <Tabs
        key={client.id}
        tabs={[
          [
            "Report",
            () => (
              <>
                {fy && <Caption>FY {fy} compared with the previous year. Change the Viewing year in the sidebar.</Caption>}
                <ClientReport client={client} fy={fy} />
              </>
            ),
          ],
          [
            "TDS",
            () => (
              <>
                <Caption>TDS Applicability, FY {fy}: one row per party per section. Select a row for the full working.</Caption>
                <TdsReport client={client} fy={fy} />
              </>
            ),
          ],
          [
            "Data coverage",
            () => (
              <>
                <ClientCoverage clientId={client.id} />
                {adding ? (
                  <AddDataPanel client={client} onClose={() => setAdding(false)} />
                ) : (
                  <Button primary onClick={() => setAdding(true)}>
                    + Add data
                  </Button>
                )}
              </>
            ),
          ],
          ["Profile", () => <ProfileForm client={client} />],
          [
            "Manage client",
            () => (
              <div className="row">
                <Button onClick={() => setDialog("rename")}>✎ Rename</Button>
                <Button onClick={() => setDialog("delete")}>🗑 Delete</Button>
              </div>
            ),
          ],
        ]}
      />
      {dialog === "rename" && <RenameDialog client={client} onClose={() => setDialog(null)} />}
      {dialog === "delete" && <DeleteDialog client={client} onClose={() => setDialog(null)} />}
    </>
  );
}
