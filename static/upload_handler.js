
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('button[data-target-input][data-action]').forEach(button => {
        button.addEventListener('click', function() {
            const targetInputId = this.dataset.targetInput;
            const action = this.dataset.action;
            const fileInput = document.getElementById(targetInputId);

            if (!fileInput) {
                console.error('Target file input not found:', targetInputId);
                return;
            }

            // Reset attributes first to ensure clean state
            fileInput.removeAttribute('capture');
            fileInput.removeAttribute('accept');

            if (action === 'camera') {
                fileInput.setAttribute('capture', 'environment'); // Prefer rear camera
                fileInput.setAttribute('accept', 'image/*'); // Ensure only images are selectable for camera
            } else if (action === 'gallery') {
                // No capture attribute for gallery
                fileInput.setAttribute('accept', 'image/*'); // Ensure only images are selectable for gallery
            }

            // Trigger the click on the hidden file input
            fileInput.click();
        });
    });
});
