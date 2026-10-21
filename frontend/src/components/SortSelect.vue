<script setup>
defineProps({
  modelValue: { type: String, default: 'relevance' },
})
const emit = defineEmits(['update:modelValue'])
</script>

<template>
  <label class="sort-select">
    <span>排序</span>
    <select :value="modelValue" @change="emit('update:modelValue', $event.target.value)">
      <option value="relevance">默认排序</option>
      <option value="time">按时间</option>
      <option value="name">按名称</option>
    </select>
  </label>
</template>
