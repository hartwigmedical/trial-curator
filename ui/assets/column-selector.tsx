import React, { useState, useEffect, useRef, ReactNode } from 'react';
import {
  draggable,
  dropTargetForElements,
  monitorForElements,
} from '@atlaskit/pragmatic-drag-and-drop/element/adapter';

type ColumnId = string;

interface DragData {
  id: string;
  sourceZone: 'available' | 'selected';
  type: string;
}

interface DropTargetData {
  id?: string;
  type?: string;
  sourceZone?: 'available' | 'selected';
  zoneId?: string;
}

interface DragEndPayload {
  type: 'reorder' | 'insert' | 'move';
  column: string;
  oldIndex?: number;
  newIndex?: number;
  source?: string;
  destinationIndex?: number;
}

function reorder<T>(list: T[], startIndex: number, finishIndex: number): T[] {
  const result = Array.from(list);
  const [removed] = result.splice(startIndex, 1);
  result.splice(finishIndex, 0, removed);
  return result;
}

interface ForEachProps<T> {
  items: T[];
  children: (item: T, index: number) => ReactNode;
}

const ForEach = <T,>({ items, children }: ForEachProps<T>) => {
  return <>{items.map((item, index) => children(item, index))}</>;
};

interface DraggableItemProps {
  id: string;
  children: ReactNode;
  sourceZone: 'available' | 'selected';
}

function DraggableItem({ id, children, sourceZone }: DraggableItemProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isDraggedOver, setIsDraggedOver] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const cleanup1 = draggable({
      element,
      getInitialData: (): DragData => ({ id, sourceZone, type: 'draggable-item' }),
      onDragStart: () => setIsDragging(true),
      onDrop: () => setIsDragging(false),
    });

    let cleanup2 = () => {};
    if (sourceZone === 'selected') {
      cleanup2 = dropTargetForElements({
        element,
        getData: (): DropTargetData => ({ id, type: 'sortable-item', sourceZone }),
        canDrop: ({ source }) => source.data.id !== id,
        onDragEnter: () => setIsDraggedOver(true),
        onDragLeave: () => setIsDraggedOver(false),
        onDrop: () => setIsDraggedOver(false),
      });
    }

    return () => {
      cleanup1();
      cleanup2();
    };
  }, [id, sourceZone]);

  const style: React.CSSProperties = {
    opacity: isDragging ? 0.5 : 1,
    padding: '8px 12px',
    margin: '4px 0',
    backgroundColor:
      sourceZone === 'selected'
        ? isDraggedOver
          ? '#90caf9'
          : '#bbdefb'
        : '#f5f5f5',
    border:
      sourceZone === 'selected'
        ? isDraggedOver
          ? '2px solid #1976d2'
          : '1px solid #90caf9'
        : '1px solid #ddd',
    borderRadius: '4px',
    cursor: 'grab',
    userSelect: 'none',
    transition: 'all 0.2s ease',
  };

  return (
    <div ref={ref} style={style}>
      {children}
    </div>
  );
}

interface SortableItemProps {
  id: string;
  children: ReactNode;
  sourceZone: 'available' | 'selected';
}

function SortableItem({ id, children, sourceZone }: SortableItemProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isDraggedOver, setIsDraggedOver] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const cleanup1 = draggable({
      element,
      getInitialData: (): DragData => ({ id, sourceZone, type: 'sortable-item' }),
      onDragStart: () => setIsDragging(true),
      onDrop: () => setIsDragging(false),
    });

    const cleanup2 = dropTargetForElements({
      element,
      getData: (): DropTargetData => ({ id, type: 'sortable-item', sourceZone }),
      canDrop: ({ source }) => source.data.id !== id,
      onDragEnter: () => setIsDraggedOver(true),
      onDragLeave: () => setIsDraggedOver(false),
      onDrop: () => setIsDraggedOver(false),
    });

    return () => {
      cleanup1();
      cleanup2();
    };
  }, [id, sourceZone]);

  const style: React.CSSProperties = {
    opacity: isDragging ? 0.5 : 1,
    padding: '8px 12px',
    margin: '4px 0',
    backgroundColor: isDraggedOver ? '#90caf9' : '#bbdefb',
    border: isDraggedOver ? '2px solid #1976d2' : '1px solid #90caf9',
    borderRadius: '4px',
    cursor: 'grab',
    userSelect: 'none',
    transition: 'all 0.2s ease',
  };

  return (
    <div ref={ref} style={style}>
      {children}
    </div>
  );
}

interface DroppableZoneProps {
  id: string;
  children: ReactNode;
  title: string;
  isSelected?: boolean;
}

function DroppableZone({ id, children, title, isSelected = false }: DroppableZoneProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [isOver, setIsOver] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    return dropTargetForElements({
      element,
      getData: (): DropTargetData => ({ zoneId: id }),
      onDragEnter: () => setIsOver(true),
      onDragLeave: () => setIsOver(false),
      onDrop: () => setIsOver(false),
    });
  }, [id]);

  const style: React.CSSProperties = {
    minHeight: '200px',
    width: '200px',
    border: isOver ? '2px solid #007bff' : '2px dashed #ccc',
    borderRadius: '8px',
    padding: '16px',
    backgroundColor: isSelected ? '#e3f2fd' : '#fafafa',
    transition: 'border-color 0.2s ease, background-color 0.2s ease',
  };

  return (
    <div>
      <h3 style={{ marginBottom: '8px' }}>{title}</h3>
      <div ref={ref} style={style}>
        {children}
      </div>
    </div>
  );
}

interface PragmaticDndWrapperProps {
  availableColumns: ColumnId[];
  selectedColumns: ColumnId[];
  onDragEnd?: (payload: DragEndPayload) => void;
}

export default function PragmaticDndWrapper({
  availableColumns,
  selectedColumns,
  onDragEnd,
}: PragmaticDndWrapperProps) {
  useEffect(() => {
    return monitorForElements({
      onDrop({ source, location }) {
        const sourceData = source.data as DragData;
        const destination = location.current.dropTargets[0];

        if (!destination) return;

        const destinationData = destination.data as DropTargetData;
        const sourceId = sourceData.id;
        const sourceZone = sourceData.sourceZone;
        const columnName = sourceId.replace('draggable-', '');

        if (destinationData.type === 'sortable-item') {
          const targetColumn = destinationData.id!.replace('draggable-', '');

          if (sourceZone === 'selected' && targetColumn !== columnName) {
            const oldIndex = selectedColumns.indexOf(columnName);
            const newIndex = selectedColumns.indexOf(targetColumn);

            if (oldIndex !== -1 && newIndex !== -1) {
              const reorderedColumns = reorder(
                selectedColumns,
                oldIndex,
                newIndex
              );

              onDragEnd?.({
                type: 'reorder',
                column: columnName,
                oldIndex,
                newIndex,
              });
              return;
            }
          } else if (sourceZone === 'available') {
            const targetIndex = selectedColumns.indexOf(targetColumn);

            if (targetIndex !== -1) {
              const newSelectedColumns = [...selectedColumns];
              newSelectedColumns.splice(targetIndex, 0, columnName);

              onDragEnd?.({
                type: 'insert',
                column: columnName,
                source: sourceZone,
                destinationIndex: targetIndex,
              });
              return;
            }
          }
        }

        if (destinationData.zoneId) {
          onDragEnd?.({
            type: 'move',
            column: columnName,
            source: sourceZone,
          });
        }
      },
    });
  }, [selectedColumns, onDragEnd]);

  return (
    <div style={{ display: 'flex', gap: '24px', justifyContent: 'center' }}>
      <DroppableZone id="available-droppable" title="Available Columns">
        <ForEach items={availableColumns}>
          {(column) => (
            <DraggableItem
              key={column}
              id={`draggable-${column}`}
              sourceZone="available"
            >
              {column.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}
            </DraggableItem>
          )}
        </ForEach>
      </DroppableZone>

      <DroppableZone id="selected-droppable" title="Selected Columns" isSelected={true}>
        <ForEach items={selectedColumns}>
          {(column) => (
            <DraggableItem
              key={column}
              id={`draggable-${column}`}
              sourceZone="selected"
            >
              {column.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}
            </DraggableItem>
          )}
        </ForEach>
      </DroppableZone>
    </div>
  );
}
