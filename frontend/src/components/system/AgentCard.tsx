"use client";

interface AgentCardProps {
  name: string;
  status: string;
  lastHeartbeat: string;
  messagesProcessed: number;
}

const STATUS_COLORS: Record<string, string> = {
  running: "bg-green-100 text-green-800",
  stopped: "bg-gray-100 text-gray-800",
  error: "bg-red-100 text-red-800",
};

export function AgentCard({
  name,
  status,
  lastHeartbeat,
  messagesProcessed,
}: AgentCardProps) {
  const colorClass = STATUS_COLORS[status] ?? "bg-gray-100 text-gray-800";

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-medium text-base capitalize">
          {name.replace(/_/g, " ")}
        </h3>
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${colorClass}`}>
          {status}
        </span>
      </div>
      <div className="text-sm text-gray-500 space-y-1">
        <div>Messages: {messagesProcessed}</div>
        <div>Last heartbeat: {lastHeartbeat || "N/A"}</div>
      </div>
    </div>
  );
}
