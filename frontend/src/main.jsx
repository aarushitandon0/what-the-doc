import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import { ScreenFailed } from './components.jsx';
import { initTelemetry, reportError } from './telemetry.js';
import './styles.css';
initTelemetry();
createRoot(document.getElementById('root'), {
  onCaughtError: (error) => { reportError(error, 'react.caught'); console.error(error); },
  onUncaughtError: (error) => { reportError(error, 'react.uncaught'); console.error(error); },
}).render(<React.StrictMode><ScreenFailed><App/></ScreenFailed></React.StrictMode>);
