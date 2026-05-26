#!/usr/bin/env node
const { spawn } = require('child_process');
const path = require('path');

const serverPath = path.join(__dirname, '..', 'server.py');
const python = process.env.PYTHON_PATH || 'python3';

const child = spawn(python, [serverPath], {
  stdio: 'inherit',
  cwd: path.join(__dirname, '..')
});

child.on('error', (err) => {
  console.error('Failed to start MCP server:', err.message);
  process.exit(1);
});

process.on('SIGINT', () => child.kill('SIGINT'));
process.on('SIGTERM', () => child.kill('SIGTERM'));
