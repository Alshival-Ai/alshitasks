export {};
declare global {
  interface Window {
    tasks: {
      request: (path: string, method?: string, body?: unknown) => Promise<any>;
      windowAction: (
        action: "state" | "minimize" | "maximize" | "close",
      ) => Promise<boolean>;
      theme: (preference: string) => Promise<void>;
      onWindowState: (callback: (maximized: boolean) => void) => () => void;
      onChanged: (callback: () => void) => () => void;
      connect: () => Promise<any>;
      importAttachment: (item: string, replaces?: string) => Promise<any>;
      importSshKey: (
        resource: string,
        settings: Record<string, unknown>,
      ) => Promise<any>;
      saveAttachment: (id: string) => Promise<any>;
      backup: () => Promise<any>;
      restore: () => Promise<any>;
      external: (url: string) => Promise<any>;
      service: (action: string) => Promise<any>;
    };
  }
}
