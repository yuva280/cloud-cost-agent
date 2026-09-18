import React, { useState } from 'react';
import { Scenario } from '../types/schemas';
import { scenarios } from '../data/scenarios';
import { Play, RotateCcw, Send } from 'lucide-react';

interface Props {
  scenario: Scenario | null;
  onRunScenario: (id: string, report?: any) => void;
  onClear: () => void;
}

export default function SystemStatus({ scenario, onRunScenario, onClear }: Props) {
  const [request, setRequest] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [statusMessage, setStatusMessage] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const serviceCount = scenario ? Object.keys(scenario.initialState).length : 3;

  let totalCost = scenario
    ? Object.values(scenario.initialState).reduce(
        (acc, s) => acc + s.cost_per_hour * s.current_instances,
        0
      )
    : 154.5;

  if (
    scenario?.workflow.final_verification.is_successful &&
    scenario.workflow.final_verification.execution?.action === 'scale_down'
  ) {
    totalCost -= 6.0;
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setSelectedFile(e.target.files[0]);
    }
  };

  const handleNaturalLanguageRequest = async () => {
    const text = request.trim();
    if (!text || isSubmitting) return;

    setIsSubmitting(true);
    setStatusMessage('Running...');

    let environmentData = undefined;
    if (selectedFile) {
      try {
        const fileContent = await selectedFile.text();
        const parsed = JSON.parse(fileContent);
        if (Array.isArray(parsed)) {
          environmentData = { services: parsed };
        } else if (parsed && typeof parsed === 'object' && !parsed.services) {
          environmentData = { services: [parsed] };
        } else {
          environmentData = parsed;
        }
      } catch (err) {
        setStatusMessage('Error parsing JSON file.');
        setIsSubmitting(false);
        return;
      }
    }

    try {
      const response = await fetch('http://localhost:8000/api/agent/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request: text, environment: environmentData }),
      });

      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }

      const data = await response.json();
      
      setStatusMessage(data.message);
      onRunScenario('dynamic-run', data);
      
    } catch (error: any) {
      console.error(error);
      setStatusMessage(`Error: ${error.message}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      handleNaturalLanguageRequest();
    }
  };

  return (
    <div
      className="glass-panel"
      style={{ padding: '24px', marginBottom: '24px' }}
    >
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '1.25rem', marginBottom: '8px' }}>
          Cloud Cost Optimization Agent
        </h2>

        <p className="text-muted" style={{ marginBottom: '14px' }}>
          Describe what you want the agent to investigate.
        </p>

        <div
          className="flex items-center gap-2"
          style={{ width: '100%' }}
        >
          <input
            type="text"
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="e.g. Reduce cloud costs without affecting availability"
            style={{
              flex: 1,
              padding: '12px 14px',
              borderRadius: '8px',
              border: '1px solid var(--border-color)',
              background: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              outline: 'none',
              fontSize: '0.95rem',
            }}
          />
          
          <div style={{ position: 'relative', overflow: 'hidden' }}>
             <input 
               type="file" 
               accept=".json" 
               onChange={handleFileChange}
               style={{
                 position: 'absolute',
                 left: 0,
                 top: 0,
                 opacity: 0,
                 cursor: 'pointer',
                 height: '100%',
                 width: '100%'
               }}
             />
             <button className="btn btn-secondary" style={{ pointerEvents: 'none' }}>
                {selectedFile ? selectedFile.name : 'Upload JSON'}
             </button>
          </div>

          <button
            onClick={handleNaturalLanguageRequest}
            className={`btn flex items-center gap-2 ${isSubmitting ? 'btn-active' : 'btn-primary'}`}
            disabled={!request.trim() || isSubmitting}
          >
            <Send size={16} />
            {isSubmitting ? 'Running...' : 'Run Agent'}
          </button>
        </div>
        
        {statusMessage && (
          <div style={{ marginTop: '12px', fontSize: '0.875rem', color: statusMessage.startsWith('Error') ? '#f87171' : 'var(--accent-blue)' }}>
            {statusMessage}
          </div>
        )}
      </div>

      <div
        className="flex justify-between items-center"
        style={{ marginBottom: '24px' }}
      >
        <h3 style={{ fontSize: '1rem' }}>Demo Scenarios</h3>

        <div className="flex gap-2">
          {scenarios.map((s) => (
            <button
              key={s.id}
              onClick={() => onRunScenario(s.id)}
              className={`btn ${
                scenario?.id === s.id
                  ? 'btn-active animate-pulse-glow'
                  : ''
              } flex items-center gap-2`}
            >
              <Play size={16} />
              {s.name}
            </button>
          ))}

          {scenario && (
            <button
              onClick={onClear}
              className="btn flex items-center gap-2"
              style={{ marginLeft: '12px' }}
            >
              <RotateCcw size={16} />
              Reset
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-6">
        <div className="glass-card">
          <div className="text-muted" style={{ marginBottom: '4px' }}>
            Total Est. Cost
          </div>

          <div
            style={{
              fontSize: '2rem',
              fontWeight: 700,
              color: 'var(--accent-green)',
            }}
          >
            ${totalCost.toFixed(2)}
            <span
              style={{
                fontSize: '1rem',
                color: 'var(--text-secondary)',
              }}
            >
              /hr
            </span>
          </div>
        </div>

        <div className="glass-card">
          <div className="text-muted" style={{ marginBottom: '4px' }}>
            Services Monitored
          </div>

          <div style={{ fontSize: '2rem', fontWeight: 700 }}>
            {serviceCount}
          </div>
        </div>

        <div className="glass-card">
          <div className="text-muted" style={{ marginBottom: '4px' }}>
            Active Recommendations
          </div>

          <div
            style={{
              fontSize: '2rem',
              fontWeight: 700,
              color: scenario
                ? 'var(--accent-blue)'
                : 'var(--text-primary)',
            }}
          >
            {scenario ? '1' : '0'}
          </div>
        </div>

        <div className="glass-card">
          <div className="text-muted" style={{ marginBottom: '4px' }}>
            Last Workflow Status
          </div>

          <div style={{ marginTop: '8px' }}>
            {!scenario && (
              <span className="badge badge-neutral">Idle</span>
            )}

            {scenario?.workflow.final_verification.is_successful && (
              <span className="badge badge-success">
                Verified Success
              </span>
            )}

            {scenario?.workflow.final_verification.is_successful ===
              false && (
              <span className="badge badge-error">
                Action Rejected / Failed
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}