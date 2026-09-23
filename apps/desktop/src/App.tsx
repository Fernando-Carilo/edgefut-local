import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter, Route, Routes } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { BootGate } from "@/components/Onboarding";
import { BacktestPage } from "@/pages/BacktestPage";
import { Dashboard } from "@/pages/Dashboard";
import { EntriesPage } from "@/pages/EntriesPage";
import { EventsPage } from "@/pages/EventsPage";
import { MatchPage } from "@/pages/MatchPage";
import { FavoritesPage, HistoryPage, LivePage, ModelsPage, PerformancePage, SourcesPage } from "@/pages/MiscPages";
import { MultiplesPage } from "@/pages/MultiplesPage";
import { RadarPage } from "@/pages/RadarPage";
import { SettingsPage } from "@/pages/SettingsPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15000 } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <BootGate>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="radar" element={<RadarPage />} />
              <Route path="jogos" element={<EventsPage />} />
              <Route path="jogos/:id" element={<MatchPage />} />
              <Route path="entradas" element={<EntriesPage />} />
              <Route path="ao-vivo" element={<LivePage />} />
              <Route path="multiplas" element={<MultiplesPage />} />
              <Route path="lab" element={<BacktestPage />} />
              <Route path="historico" element={<HistoryPage />} />
              <Route path="favoritos" element={<FavoritesPage />} />
              <Route path="fontes" element={<SourcesPage />} />
              <Route path="modelos" element={<ModelsPage />} />
              <Route path="performance" element={<PerformancePage />} />
              <Route path="configuracoes" element={<SettingsPage />} />
            </Route>
          </Routes>
        </BootGate>
      </HashRouter>
    </QueryClientProvider>
  );
}
