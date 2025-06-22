import React, { useState, useEffect, useRef } from 'react';
import { draggable, dropTargetForElements, monitorForElements } from '@atlaskit/pragmatic-drag-and-drop/element/adapter';

// Simple reorder utility function
function reorder(list, startIndex, finishIndex) {
  const result = Array.from(list);
  const [removed] = result.splice(startIndex, 1);
  result.splice(finishIndex, 0, removed);
  return result;
}

const ForEach = ({ items, children }) => {
  return items.map((item, index) => children(item, index));
};

function DraggableItem({ id, children, sourceZone }) {
  const ref = useRef(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isDraggedOver, setIsDraggedOver] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const cleanup1 = draggable({
      element,
      getInitialData: () => ({ id, sourceZone, type: 'draggable-item' }),
      onDragStart: () => setIsDragging(true),
      onDrop: () => setIsDragging(false),
    });

    // Only make it a drop target if it's in the selected zone (for reordering)
    let cleanup2 = () => {};
    if (sourceZone === 'selected') {
      cleanup2 = dropTargetForElements({
        element,
        getData: () => ({ id, type: 'sortable-item', sourceZone }),
        canDrop: ({ source }) => {
          return source.data.id !== id; // Can't drop on itself
        },
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

  const style = {
    opacity: isDragging ? 0.5 : 1,
    padding: '8px 12px',
    margin: '4px 0',
    backgroundColor: sourceZone === 'selected'
      ? (isDraggedOver ? '#90caf9' : '#bbdefb')
      : '#f5f5f5',
    border: sourceZone === 'selected'
      ? (isDraggedOver ? '2px solid #1976d2' : '1px solid #90caf9')
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

function SortableItem({ id, children, sourceZone }) {
  const ref = useRef(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isDraggedOver, setIsDraggedOver] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const cleanup1 = draggable({
      element,
      getInitialData: () => ({ id, sourceZone, type: 'sortable-item' }),
      onDragStart: () => setIsDragging(true),
      onDrop: () => setIsDragging(false),
    });

    const cleanup2 = dropTargetForElements({
      element,
      getData: () => ({ id, type: 'sortable-item', sourceZone }),
      canDrop: ({ source }) => {
        return source.data.id !== id; // Can't drop on itself
      },
      onDragEnter: () => setIsDraggedOver(true),
      onDragLeave: () => setIsDraggedOver(false),
      onDrop: () => setIsDraggedOver(false),
    });

    return () => {
      cleanup1();
      cleanup2();
    };
  }, [id, sourceZone]);

  const style = {
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

function DroppableZone({ id, children, title, isSelected = false }) {
  const ref = useRef(null);
  const [isOver, setIsOver] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    return dropTargetForElements({
      element,
      getData: () => ({ zoneId: id }),
      onDragEnter: () => setIsOver(true),
      onDragLeave: () => setIsOver(false),
      onDrop: () => setIsOver(false),
    });
  }, [id]);

  const style = {
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

export default function PragmaticDndWrapper({ availableColumns, selectedColumns, onDragEnd }) {
  useEffect(() => {
    return monitorForElements({
      onDrop({ source, location }) {
        const sourceData = source.data;
        const destination = location.current.dropTargets[0];

        if (!destination) return;

        const destinationData = destination.data;
        const sourceId = sourceData.id;
        const sourceZone = sourceData.sourceZone;

        // Extract column name from draggable ID
        const columnName = sourceId.replace('draggable-', '');

        // Handle dropping on specific items (reordering or inserting at position)
        if (destinationData.type === 'sortable-item') {
          const targetColumn = destinationData.id.replace('draggable-', '');

          if (sourceZone === 'selected' && targetColumn !== columnName) {
            // Reordering within selected columns
            const oldIndex = selectedColumns.indexOf(columnName);
            const newIndex = selectedColumns.indexOf(targetColumn);

            if (oldIndex !== -1 && newIndex !== -1) {
              const reorderedColumns = reorder(
                selectedColumns,
                oldIndex,
                newIndex
              );

              if (onDragEnd) {
                onDragEnd({
                  type: 'reorder',
                  column: columnName,
                  oldIndex: oldIndex,
                  newIndex: newIndex
                });
              }
              return;
            }
          } else if (sourceZone === 'available') {
            // Inserting from available to specific position in selected
            const targetIndex = selectedColumns.indexOf(targetColumn);

            if (targetIndex !== -1) {
              const newSelectedColumns = [...selectedColumns];
              newSelectedColumns.splice(targetIndex, 0, columnName);

              if (onDragEnd) {
                onDragEnd({
                  type: 'insert',
                  column: columnName,
                  source: sourceZone,
                  destinationIndex: targetIndex
                });
              }
              return;
            }
          }
        }

        // Handle dropping on zones (append to end)
        if (destinationData.zoneId) {
          if (onDragEnd) {
            onDragEnd({
              type: 'move',
              column: columnName,
              source: sourceZone
            });
          }
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