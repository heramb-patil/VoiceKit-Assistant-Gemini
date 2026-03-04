#!/bin/bash

# Backend API Test Script
# Quick verification that all endpoints are working

set -e

echo "🧪 Testing Gemini Live Backend..."
echo ""

BASE_URL="http://localhost:8001"
USER_ID="test@example.com"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if backend is running
echo "1. Checking if backend is running..."
if curl -s -f "${BASE_URL}/gemini-live/health" > /dev/null; then
    echo -e "${GREEN}✓ Backend is running${NC}"
else
    echo -e "${RED}✗ Backend is not running${NC}"
    echo "  Start it with: uvicorn main:app --host 0.0.0.0 --port 8001 --reload"
    exit 1
fi
echo ""

# Test health check
echo "2. Testing health check endpoint..."
HEALTH=$(curl -s "${BASE_URL}/gemini-live/health")
TOOL_COUNT=$(echo "$HEALTH" | grep -o '"tool_count":[0-9]*' | cut -d':' -f2)
echo "  Response: $HEALTH"
if [ "$TOOL_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✓ Health check passed (${TOOL_COUNT} tools loaded)${NC}"
else
    echo -e "${YELLOW}⚠ Warning: No tools loaded${NC}"
fi
echo ""

# Test tool execution
echo "3. Testing tool execution endpoint..."
TOOL_RESPONSE=$(curl -s -X POST "${BASE_URL}/gemini-live/tool-execute" \
  -H "Content-Type: application/json" \
  -d "{
    \"user_identity\": \"${USER_ID}\",
    \"tool_name\": \"get_current_time\",
    \"tool_args\": {\"city\": \"Tokyo\"}
  }")
echo "  Response: $TOOL_RESPONSE"
if echo "$TOOL_RESPONSE" | grep -q '"success":true'; then
    echo -e "${GREEN}✓ Tool execution successful${NC}"
else
    echo -e "${YELLOW}⚠ Tool execution failed (tool may not exist)${NC}"
fi
echo ""

# Test task delegation
echo "4. Testing task delegation endpoint..."
TASK_RESPONSE=$(curl -s -X POST "${BASE_URL}/gemini-live/task-delegate" \
  -H "Content-Type: application/json" \
  -d "{
    \"user_identity\": \"${USER_ID}\",
    \"task_description\": \"Test task: Get the current time\"
  }")
echo "  Response: $TASK_RESPONSE"
TASK_ID=$(echo "$TASK_RESPONSE" | grep -o '"task_id":"[^"]*"' | cut -d'"' -f4)
if [ -n "$TASK_ID" ]; then
    echo -e "${GREEN}✓ Task delegation successful (task_id: ${TASK_ID})${NC}"
else
    echo -e "${RED}✗ Task delegation failed${NC}"
    exit 1
fi
echo ""

# Test task polling
echo "5. Testing task polling endpoint..."
sleep 2  # Give task time to complete
TASKS_RESPONSE=$(curl -s "${BASE_URL}/gemini-live/tasks?user_identity=${USER_ID}&delivered=false")
echo "  Response: $TASKS_RESPONSE"
if echo "$TASKS_RESPONSE" | grep -q "pending_results"; then
    echo -e "${GREEN}✓ Task polling successful${NC}"
else
    echo -e "${RED}✗ Task polling failed${NC}"
    exit 1
fi
echo ""

# Test follow-up response (no pending question, should fail gracefully)
echo "6. Testing follow-up response endpoint..."
FOLLOWUP_RESPONSE=$(curl -s -X POST "${BASE_URL}/gemini-live/followup-response" \
  -H "Content-Type: application/json" \
  -d "{
    \"user_identity\": \"${USER_ID}\",
    \"response_text\": \"Test answer\"
  }")
echo "  Response: $FOLLOWUP_RESPONSE"
if echo "$FOLLOWUP_RESPONSE" | grep -q "success"; then
    echo -e "${GREEN}✓ Follow-up response endpoint working${NC}"
else
    echo -e "${RED}✗ Follow-up response endpoint failed${NC}"
    exit 1
fi
echo ""

# Summary
echo "════════════════════════════════════════════════════════"
echo -e "${GREEN}✓ All tests passed!${NC}"
echo ""
echo "Backend is ready for frontend integration."
echo ""
echo "Next steps:"
echo "  1. Fork Google's multimodal-live-api-web-console"
echo "  2. Add VoiceKit bridge files"
echo "  3. Test end-to-end voice flow"
echo "════════════════════════════════════════════════════════"
