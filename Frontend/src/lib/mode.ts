// Mock mode: fake login + fake API so the demo can be clicked through before the backend exists.
// `import.meta.env.DEV` is false in every `vite build`, so this is always false in a production bundle
// and the bundler drops the mock code entirely. vite.config.js also refuses to build with VITE_MOCK_API=1.
export const MOCK = import.meta.env.DEV && import.meta.env.VITE_MOCK_API === "1";
