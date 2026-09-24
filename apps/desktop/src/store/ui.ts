import type { MultipleLeg } from "@edgefut/contracts";
import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface BuilderLeg extends MultipleLeg {
  label: string;
  event_label: string;
  model_prob?: number | null;
}

interface UiState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  simulations: number;
  setSimulations: (n: number) => void;
  builder: BuilderLeg[];
  addLeg: (leg: BuilderLeg) => void;
  removeLeg: (idx: number) => void;
  clearBuilder: () => void;
  onboarded: boolean;
  setOnboarded: (v: boolean) => void;
}

export const useUi = create<UiState>()(
  persist(
    (set, get) => ({
      sidebarCollapsed: false,
      toggleSidebar: () => set({ sidebarCollapsed: !get().sidebarCollapsed }),
      simulations: 50000,
      setSimulations: (n) => set({ simulations: n }),
      builder: [],
      addLeg: (leg) => {
        const exists = get().builder.some(
          (l) => l.event_id === leg.event_id && l.market_key === leg.market_key && l.selection_key === leg.selection_key && l.line === leg.line,
        );
        if (exists || get().builder.length >= 6) return;
        set({ builder: [...get().builder, leg] });
      },
      removeLeg: (idx) => set({ builder: get().builder.filter((_, i) => i !== idx) }),
      clearBuilder: () => set({ builder: [] }),
      onboarded: false,
      setOnboarded: (v) => set({ onboarded: v }),
    }),
    { name: "edgefut-ui" },
  ),
);
