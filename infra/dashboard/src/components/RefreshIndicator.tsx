'use client';
import { fmtTime } from '@/lib/formatters';
import { RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';

interface Props {
  lastUpdate: Date | null;
  nextRefreshIn: number; // seconds
  isRefreshing: boolean;
}

export function RefreshIndicator({ lastUpdate, nextRefreshIn, isRefreshing }: Props) {
  return (
    <div className="fixed bottom-4 right-4 flex items-center gap-2 rounded-full border border-gray-800 bg-[#111318] px-3 py-1.5 text-xs font-mono text-gray-500 shadow-lg">
      <RefreshCw
        className={cn('h-3 w-3', isRefreshing ? 'animate-spin text-green-400' : 'text-gray-600')}
      />
      <span>
        {lastUpdate ? `Updated ${fmtTime(lastUpdate.toISOString())}` : 'Connecting…'}
      </span>
      {!isRefreshing && (
        <span className="text-gray-700">· {nextRefreshIn}s</span>
      )}
    </div>
  );
}
