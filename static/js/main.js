document.addEventListener('DOMContentLoaded', function() {
    const messagesContainer = document.querySelector('.messages-container');
    const currentContact = new URLSearchParams(window.location.search).get('contact');
    let currentPage = 1;
    let loading = false;
    let hasNextPage = true;

    if (messagesContainer) {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;

        // Infinite scroll
        messagesContainer.addEventListener('scroll', function() {
            if (loading || !hasNextPage) return;

            const threshold = 100;
            if (messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight < threshold) {
                loading = true;
                currentPage++;

                const searchParams = new URLSearchParams(window.location.search);
                searchParams.set('page', currentPage);

                fetch(`/?${searchParams.toString()}`, {
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.messages && data.messages.length > 0) {
                        data.messages.forEach(message => {
                            const messageDiv = document.createElement('div');
                            messageDiv.className = `message ${message.is_sent ? 'sent' : 'received'}`;
                            messageDiv.innerHTML = `
                                <div class="message-content">
                                    ${message.body}
                                    <div class="message-time">
                                        ${new Date(message.date).toLocaleString()}
                                    </div>
                                </div>
                            `;
                            messagesContainer.appendChild(messageDiv);
                        });
                        hasNextPage = data.has_next;
                    } else {
                        hasNextPage = false;
                    }
                })
                .catch(error => console.error('Error:', error))
                .finally(() => {
                    loading = false;
                });
            }
        });
    }

    // Add active class to current contact
    if (currentContact) {
        const contactLinks = document.querySelectorAll('.contact-item');
        contactLinks.forEach(link => {
            if (link.href.includes(encodeURIComponent(currentContact))) {
                link.classList.add('active');
            }
        });
    }
});
