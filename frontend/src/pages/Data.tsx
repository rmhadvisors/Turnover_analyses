/** Data: import Tally exports or enter yearly figures for the sidebar's client. */

import { useWorkspace, type Row } from "../api";
import { ClientCoverage } from "../components/Coverage";
import ManualEntry from "../components/ManualEntry";
import RegisterImport from "../components/RegisterImport";
import { AddDataPanel, ImportLog } from "../components/TallyImport";
import { useSelection } from "../state";
import { Expander, Notice, PageHeader, Tabs } from "../ui";

export default function DataPage() {
  const { clientId, fy } = useSelection();
  const client: Row | undefined = useWorkspace().data?.clients.find((c: Row) => c.id === clientId);
  return (
    <>
      <PageHeader title="Data" subtitle="Import Tally data or enter yearly figures for the selected client." />
      {!client ? (
        <Notice>Choose a client in the sidebar to continue, or add one on the Clients page.</Notice>
      ) : (
        <>
          <Expander title="Data coverage: which years this client has">
            <ClientCoverage clientId={client.id} />
          </Expander>
          <Tabs
            key={client.id}
            tabs={[
              [
                "Tally import",
                () => (
                  <>
                    <AddDataPanel key={client.id} client={client} />
                    <Expander title="Other formats: Sales Register, Purchase Register, P&L (Excel / CSV)">
                      <RegisterImport key={client.id} client={client} />
                    </Expander>
                    <hr />
                    <ImportLog clientId={client.id} />
                  </>
                ),
              ],
              ["Manual entry", () => <ManualEntry client={client} fy={fy} />],
            ]}
          />
        </>
      )}
    </>
  );
}
