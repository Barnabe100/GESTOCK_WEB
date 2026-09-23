import type { Toast } from 'primereact/toast';
import { createContext, useContext, type RefObject } from 'react';

export const ToastContext = createContext<RefObject<Toast | null> | null>(null);

export function useToast() {
  const ref = useContext(ToastContext);
  return {
    success: (summary: string) => ref?.current?.show({ severity: 'success', summary, life: 3000 }),
    error: (summary: string) => ref?.current?.show({ severity: 'error', summary, life: 5000 }),
  };
}
