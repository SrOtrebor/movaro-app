
document.addEventListener('DOMContentLoaded', function() {
    // Function to detect if it's a mobile device
    function isMobileDevice() {
        return (typeof window.orientation !== "undefined") || (navigator.userAgent.indexOf('IEMobile') !== -1);
    }

    document.querySelectorAll('input[type="file"][data-camera-gallery-choice="true"]').forEach(inputElement => {
        inputElement.addEventListener('click', function(event) {
            if (isMobileDevice()) {
                event.preventDefault(); // Prevent the default file picker from opening immediately

                const fileInput = this;

                const choice = confirm("¿Deseas tomar una foto con la cámara o seleccionar de la galería?");

                if (choice) { // User chose Camera (or OK)
                    fileInput.removeAttribute('accept'); // Remove accept to allow all file types for camera
                    fileInput.setAttribute('capture', 'environment'); // Prefer rear camera
                    fileInput.click(); // Re-trigger the click to open camera
                } else { // User chose Gallery (or Cancel)
                    fileInput.removeAttribute('capture'); // Remove capture to open gallery
                    fileInput.setAttribute('accept', 'image/*'); // Ensure only images are selectable
                    fileInput.click(); // Re-trigger the click to open gallery
                }
            }
            // If not a mobile device, let the default click behavior happen
        });
    });
});
