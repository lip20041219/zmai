// Simple script for the homepage
document.addEventListener('DOMContentLoaded', function() {
    const button = document.getElementById('clickMe');
    if (button) {
        button.addEventListener('click', function() {
            alert('Button clicked!');
        });
    }
});
