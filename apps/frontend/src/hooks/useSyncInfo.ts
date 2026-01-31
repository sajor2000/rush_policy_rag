import { useState, useEffect } from "react";
import { getSyncInfo, type SyncInfo } from "@/lib/api";

/**
 * Custom hook to fetch sync information with proper cleanup.
 * Prevents memory leaks by checking if component is still mounted before setState.
 *
 * @returns SyncInfo object or null if not yet loaded or failed
 */
export function useSyncInfo(): SyncInfo | null {
  const [syncInfo, setSyncInfo] = useState<SyncInfo | null>(null);

  useEffect(() => {
    let mounted = true;

    getSyncInfo()
      .then((data) => {
        if (mounted) {
          setSyncInfo(data);
        }
      })
      .catch(() => {
        // Silently fail - sync date is informational only
      });

    return () => {
      mounted = false;
    };
  }, []);

  return syncInfo;
}
