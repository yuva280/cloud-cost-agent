import React, { useState } from 'react';
import { scenarios, getInitialStates } from './data/scenarios';
import { Scenario } from './types/schemas';
import DashboardHeader from './components/DashboardHeader';
import SystemStatus from './components/SystemStatus';
import ServicesTable from './components/ServicesTable';
import WorkflowVisualizer from './components/WorkflowVisualizer';
import Panels from './components/Panels';

export default function App() {
  const [activeScenario, setActiveScenario] = useState<Scenario | null>(null);

  const handleRunScenario = (scenarioId: string, nlpData?: { report: any, original_request: string, identified_service: string, message: string }) => {
    if (nlpData) {
      // Dynamic scenario generated from the real backend NL request
      const { report, original_request, identified_service, message } = nlpData;
      
      const allStates = getInitialStates();
      // If we have the initial state in our static mock, use it for the table. Otherwise, we just map the initial_observation to a fake ServiceState.
      const serviceState = allStates[identified_service] || {
        ...report.initial_observation,
        min_instances: 1,
        max_instances: 10,
        current_instances: 3,
        healthy: true,
        is_critical: false,
        service_type: "unknown"
      };

      const dynamicScenario: Scenario = {
        id: "dynamic-run",
        name: `Agent Run: "${original_request}"`,
        description: message,
        initialState: {
          [identified_service]: serviceState
        },
        workflow: report
      };
      
      setActiveScenario(dynamicScenario);
    } else {
      // Fallback to static mock scenarios
      const scenario = scenarios.find(s => s.id === scenarioId);
      if (scenario) {
        setActiveScenario(scenario);
      }
    }
  };

  const handleClear = () => setActiveScenario(null);

  return (
    <div className="container flex-col gap-6 fade-in">
      <DashboardHeader />
      
      <SystemStatus 
        scenario={activeScenario} 
        onRunScenario={handleRunScenario}
        onClear={handleClear}
      />

      {activeScenario && (
        <>
          <WorkflowVisualizer workflow={activeScenario.workflow} />
          
          <div className="grid grid-cols-2 gap-6" style={{ gridTemplateColumns: '1fr 1fr' }}>
            <div className="flex-col gap-4">
              <h2 style={{ fontSize: '1.25rem', marginBottom: '8px' }}>Monitored Services</h2>
              <ServicesTable initialState={activeScenario.initialState} workflow={activeScenario.workflow} />
            </div>
            
            <div className="flex-col gap-4">
              <h2 style={{ fontSize: '1.25rem', marginBottom: '8px' }}>Workflow Details</h2>
              <Panels workflow={activeScenario.workflow} />
            </div>
          </div>
        </>
      )}

      {!activeScenario && (
        <div className="glass-panel" style={{ padding: '64px', textAlign: 'center', marginTop: '32px' }}>
          <h2 style={{ color: 'var(--text-secondary)' }}>No Active Workflow</h2>
          <p className="text-muted" style={{ marginTop: '8px' }}>Select a scenario above to simulate the Cloud Cost Optimization Agent.</p>
        </div>
      )}
    </div>
  );
}
