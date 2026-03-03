'use client';
import { StrategyData } from '@/lib/types';
import { StrategyCard } from './StrategyCard';
import { Skeleton } from '@/components/ui/skeleton';

interface Props {
  strategies: StrategyData[] | null;
}

export function StrategyGrid({ strategies }: Props) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
      {strategies == null
        ? Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-48 rounded-lg" />
          ))
        : strategies.map(s => <StrategyCard key={s.strategy_id} strategy={s} />)}
    </div>
  );
}
