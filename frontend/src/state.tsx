/**
 * The global selections the sidebar owns: client, financial year, amount display and the
 * reviewer name. Pages read them with `useSelection()` and never offer their own pickers.
 * They are remembered in this browser between visits.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { DISPLAY_MODES } from "./format";

interface Selection {
  clientId: number | null;
  fy: string | null;
  displayMode: string;
  reviewer: string;
  setClientId: (id: number | null) => void;
  setFy: (fy: string | null) => void;
  setDisplayMode: (mode: string) => void;
  setReviewer: (name: string) => void;
  /** The amount unit for formatting and exports: "auto" | "lakhs" | "crores" | "full". */
  unit: string;
}

const SelectionContext = createContext<Selection | null>(null);

function usePersisted<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(`ta.${key}`);
      return stored === null ? initial : (JSON.parse(stored) as T);
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(`ta.${key}`, JSON.stringify(value));
    } catch {
      // storage unavailable (private window): keep the value for this visit only
    }
  }, [key, value]);
  return [value, setValue];
}

export function SelectionProvider({ children }: { children: ReactNode }) {
  const [clientId, setClientId] = usePersisted<number | null>("clientId", null);
  const [fy, setFy] = usePersisted<string | null>("fy", null);
  const [displayMode, setDisplayMode] = usePersisted("displayMode", "Auto");
  const [reviewer, setReviewer] = usePersisted("reviewer", "");
  const unit = DISPLAY_MODES[displayMode] ?? "auto";
  return (
    <SelectionContext.Provider
      value={{ clientId, fy, displayMode, reviewer, setClientId, setFy, setDisplayMode, setReviewer, unit }}
    >
      {children}
    </SelectionContext.Provider>
  );
}

export function useSelection(): Selection {
  const selection = useContext(SelectionContext);
  if (!selection) throw new Error("useSelection outside SelectionProvider");
  return selection;
}
