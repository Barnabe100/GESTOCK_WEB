import { createBrowserRouter } from 'react-router';

import { HomePage } from '@/pages/HomePage';

// Les routes métier seront générées depuis le registre des modules frontend
// (voir docs/architecture/ARCHITECTURE.md, section 7), filtrées par les capacités
// renvoyées par le backend.
export const router = createBrowserRouter([{ path: '/', element: <HomePage /> }]);
