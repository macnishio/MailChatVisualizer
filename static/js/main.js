document.addEventListener('DOMContentLoaded', function() {
    const messagesContainer = document.querySelector('.messages-container');
    const pageSizeSelect = document.getElementById('pageSizeSelect');
    const sortSelect = document.getElementById('sortSelect');
    const searchInput = document.getElementById('contactSearch');
    const searchResults = document.getElementById('searchResults');
    let currentPage = 1;
    let loading = false;
    let hasNextPage = true;

    // ページサイズと並び替えの変更を処理する関数
    function updateUrlAndReload(params) {
        const currentUrl = new URL(window.location.href);
        const contact = currentUrl.searchParams.get('contact');
        const search = currentUrl.searchParams.get('search');

        const newParams = new URLSearchParams();
        if (contact) newParams.set('contact', contact);
        if (search) newParams.set('search', search);

        // 渡されたパラメータを追加
        Object.entries(params).forEach(([key, value]) => {
            newParams.set(key, value);
        });

        // アニメーション効果を追加してページ遷移
        document.body.style.opacity = '0.5';
        document.body.style.transition = 'opacity 0.3s ease';

        setTimeout(() => {
            window.location.href = `${window.location.pathname}?${newParams.toString()}`;
        }, 300);
    }

    // ページサイズの変更を処理
    if (pageSizeSelect) {
        pageSizeSelect.addEventListener('change', function() {
            updateUrlAndReload({
                'per_page': this.value,
                'page': 1 // ページサイズ変更時はページを1に戻す
            });
        });
    }

    // 並び替えの変更を処理
    if (sortSelect) {
        sortSelect.addEventListener('change', function() {
            updateUrlAndReload({
                'sort_by': this.value,
                'page': 1 // 並び替え変更時はページを1に戻す
            });
        });
    }

    // メッセージの展開機能
    document.querySelectorAll('.show-full-message').forEach(button => {
        button.addEventListener('click', function() {
            const preview = this.closest('.message-preview');
            const full = preview.nextElementSibling;
            preview.style.display = 'none';
            full.style.display = 'block';
        });
    });

    // Infinite scroll for messages
    if (messagesContainer) {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;

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
                        'X-Requested-With': 'XMLHttpRequest',
                        'Accept': 'application/json'
                    },
                    credentials: 'same-origin'
                })
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP error! status: ${response.status}`);
                    }
                    const contentType = response.headers.get('content-type');
                    if (!contentType || !contentType.includes('application/json')) {
                        throw new TypeError("Expected JSON response but received " + contentType);
                    }
                    return response.json();
                })
                .then(data => {
                    if (data && data.messages && Array.isArray(data.messages)) {
                        if (data.messages.length > 0) {
                            data.messages.forEach(message => {
                                const messageDiv = document.createElement('div');
                                messageDiv.className = `message ${message.is_sent ? 'sent' : 'received'}`;
                                messageDiv.innerHTML = `
                                    <div class="message-content">
                                        ${message.body || ''}
                                        <div class="message-time">
                                            ${new Date(message.date).toLocaleString()}
                                        </div>
                                    </div>
                                `;
                                messagesContainer.appendChild(messageDiv);
                            });
                        }
                        hasNextPage = Boolean(data.has_next);
                    } else {
                        hasNextPage = false;
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    hasNextPage = false;
                })
                .finally(() => {
                    loading = false;
                });
            }
        });
    }

    // Contact search functionality
    if (searchInput && searchResults) {
        let debounceTimer;

        searchInput.addEventListener('input', function() {
            clearTimeout(debounceTimer);
            const query = this.value.trim();

            if (query.length < 2) {
                searchResults.classList.remove('show');
                return;
            }

            debounceTimer = setTimeout(() => {
                const apiUrl = `/api/search_contacts?q=${encodeURIComponent(query)}`;
                fetch(apiUrl, {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    credentials: 'same-origin'
                })
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`Network response was not ok: ${response.status}`);
                    }
                    const contentType = response.headers.get('content-type');
                    if (!contentType || !contentType.includes('application/json')) {
                        throw new TypeError("Expected JSON response but received " + contentType);
                    }
                    return response.json();
                })
                .then(data => {
                    searchResults.innerHTML = '';

                    if (Array.isArray(data) && data.length > 0) {
                        data.forEach(contact => {
                            const item = document.createElement('a');
                            item.className = 'dropdown-item';
                            item.href = `/?contact=${encodeURIComponent(contact)}`;
                            item.textContent = contact;
                            item.setAttribute('role', 'option');
                            searchResults.appendChild(item);
                        });
                        searchResults.classList.add('show');
                    } else {
                        // 検索結果が空の場合のメッセージを表示
                        const noResults = document.createElement('div');
                        noResults.className = 'dropdown-item text-muted';
                        noResults.textContent = '結果が見つかりません';
                        searchResults.appendChild(noResults);
                        searchResults.classList.add('show');
                    }
                })
                .catch(error => {
                    console.error('Search error:', error);
                    // エラーメッセージを表示
                    searchResults.innerHTML = '';
                    const errorItem = document.createElement('div');
                    errorItem.className = 'dropdown-item text-danger';
                    errorItem.textContent = '検索中にエラーが発生しました';
                    searchResults.appendChild(errorItem);
                    searchResults.classList.add('show');
                });
            }, 300);
        });

        // クリック外での非表示
        document.addEventListener('click', function(e) {
            if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
                searchResults.classList.remove('show');
            }
        });

        // Escキーでの非表示
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                searchResults.classList.remove('show');
            }
        });
    }

    // Add active class to current contact
    const currentContact = new URLSearchParams(window.location.search).get('contact');
    if (currentContact) {
        const contactLinks = document.querySelectorAll('.contact-item');
        contactLinks.forEach(link => {
            if (link.href.includes(encodeURIComponent(currentContact))) {
                link.classList.add('active');
            }
        });
    }
});