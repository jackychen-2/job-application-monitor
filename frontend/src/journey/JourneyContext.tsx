import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  activateJourney as activateJourneyApi,
  createJourney as createJourneyApi,
  deleteJourney as deleteJourneyApi,
  listJourneys,
  renameJourney as renameJourneyApi,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { Journey, JourneyDeleteResult } from "../types";

interface JourneyContextValue {
  loading: boolean;
  journeys: Journey[];
  activeJourney: Journey | null;
  refreshJourneys: () => Promise<void>;
  createJourney: (name?: string) => Promise<void>;
  activateJourney: (journeyId: number) => Promise<void>;
  renameJourney: (journeyId: number, name: string) => Promise<void>;
  deleteJourney: (journeyId: number) => Promise<JourneyDeleteResult>;
}

const JourneyContext = createContext<JourneyContextValue | null>(null);

export function JourneyProvider({ children }: { children: React.ReactNode }) {
  const { user, refreshAuth } = useAuth();
  const [loading, setLoading] = useState(true);
  const [journeys, setJourneys] = useState<Journey[]>([]);

  const refreshJourneys = useCallback(async () => {
    if (!user) {
      setJourneys([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const data = await listJourneys();
      setJourneys(data);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    void refreshJourneys();
  }, [refreshJourneys]);

  const createJourney = useCallback(async (name?: string) => {
    await createJourneyApi({ name });
    await Promise.all([refreshJourneys(), refreshAuth()]);
  }, [refreshAuth, refreshJourneys]);

  const activateJourney = useCallback(async (journeyId: number) => {
    await activateJourneyApi(journeyId);
    await Promise.all([refreshJourneys(), refreshAuth()]);
  }, [refreshAuth, refreshJourneys]);

  const renameJourney = useCallback(async (journeyId: number, name: string) => {
    await renameJourneyApi(journeyId, { name });
    await refreshJourneys();
  }, [refreshJourneys]);

  const deleteJourney = useCallback(async (journeyId: number) => {
    const result = await deleteJourneyApi(journeyId);
    await Promise.all([refreshJourneys(), refreshAuth()]);
    return result;
  }, [refreshAuth, refreshJourneys]);

  const activeJourney = useMemo(() => {
    if (journeys.length === 0) return null;
    if (user?.active_journey_id != null) {
      const byId = journeys.find((j) => j.id === user.active_journey_id);
      if (byId) return byId;
    }
    return journeys.find((j) => j.is_active) ?? journeys[0];
  }, [journeys, user?.active_journey_id]);

  const value = useMemo<JourneyContextValue>(() => ({
    loading,
    journeys,
    activeJourney,
    refreshJourneys,
    createJourney,
    activateJourney,
    renameJourney,
    deleteJourney,
  }), [loading, journeys, activeJourney, refreshJourneys, createJourney, activateJourney, renameJourney, deleteJourney]);

  return <JourneyContext.Provider value={value}>{children}</JourneyContext.Provider>;
}

export function useJourney(): JourneyContextValue {
  const ctx = useContext(JourneyContext);
  if (!ctx) {
    throw new Error("useJourney must be used within JourneyProvider");
  }
  return ctx;
}
