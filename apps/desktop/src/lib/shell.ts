/**
 * Ponte mínima com a shell Tauri (iteração 5, §46–48). No browser (dev/CDP) tudo é no-op.
 * A shell só recebe duas flags — nunca credenciais nem dados de apostas.
 */
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export const inTauri = (): boolean => {
  try {
    return isTauri();
  } catch {
    return false;
  }
};

export type ShellBackgroundResult = { background_collector: boolean; autostart_on_login: boolean; autostart_active: boolean };

export async function applyBackgroundSettings(background_collector: boolean, autostart_on_login: boolean): Promise<ShellBackgroundResult | null> {
  if (!inTauri()) return null;
  try {
    return await invoke<ShellBackgroundResult>("apply_background_settings", { backgroundCollector: background_collector, autostartOnLogin: autostart_on_login });
  } catch (e) {
    console.warn("shell: apply_background_settings falhou", e);
    return null;
  }
}

/** Sair de verdade: encerra a janela, a bandeja e o engine (equivalente ao "Sair" do tray). */
export async function quitApp(): Promise<void> {
  if (!inTauri()) return;
  await invoke("quit_app");
}

/** Navegação pedida pela bandeja ("Ver Data Flywheel"). Devolve unsubscribe. */
export function onShellNavigate(cb: (path: string) => void): () => void {
  if (!inTauri()) return () => {};
  let un: (() => void) | null = null;
  let cancelled = false;
  listen<string>("navigate", (ev) => cb(ev.payload)).then((f) => {
    if (cancelled) f();
    else un = f;
  });
  return () => {
    cancelled = true;
    un?.();
  };
}
