"use client";

import { Menu } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { clsx } from "clsx";
import {
  type Citation,
  type ConversationSummary,
  type Course,
  type DocumentSummary,
  type Scope,
  deleteConversation,
  fetchConversation,
  fetchConversations,
  fetchCourses,
  fetchDocuments,
  renameConversation,
  requestAnswerStream,
} from "../lib/api";
import { type ChatMessage, type ViewerTarget, applyEvent, citationTarget, fromStored, pendingAnswer, scopeLabel } from "../lib/chat";
import { ChatPanel } from "./chat/chat-panel";
import { Sidebar } from "./sidebar";
import { DocumentViewer } from "./viewer/document-viewer";

const RETRY_MS = 5000;
let nextId = 0;
const localId = () => `local-${++nextId}`;

function readUrl(): { conversation: string | null; document: string | null; page: number } {
  if (typeof window === "undefined") return { conversation: null, document: null, page: 1 };
  const params = new URLSearchParams(window.location.search);
  return { conversation: params.get("c"), document: params.get("doc"), page: Math.max(1, Number(params.get("p")) || 1) };
}

export function Workspace() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [libraryError, setLibraryError] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [scope, setScope] = useState<Scope>({});
  const [busy, setBusy] = useState(false);
  const [viewer, setViewer] = useState<ViewerTarget | null>(null);
  const [activeCitation, setActiveCitation] = useState<{ messageId: string; number: number } | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const nonce = useRef(0);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await fetchConversations());
    } catch {
      // The list refreshes again after the next answer; the chat itself still works.
    }
  }, []);

  // Library: retry until the API answers, so a page opened while the stack starts recovers.
  useEffect(() => {
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const [loadedCourses, loadedDocuments] = await Promise.all([fetchCourses(), fetchDocuments()]);
        if (!active) return;
        setCourses(loadedCourses);
        setDocuments(loadedDocuments);
        setLibraryError(false);
        void refreshConversations();
      } catch {
        if (!active) return;
        setLibraryError(true);
        retry = setTimeout(load, RETRY_MS);
      }
    };
    void load();
    return () => {
      active = false;
      clearTimeout(retry);
    };
  }, [refreshConversations]);

  const openConversation = useCallback(async (id: string) => {
    controller.current?.abort();
    try {
      const detail = await fetchConversation(id);
      setConversationId(detail.id);
      setMessages(fromStored(detail.messages));
      setScope(detail.scope ?? {});
      setActiveCitation(null);
      setSidebarOpen(false);
    } catch {
      setConversationId(null);
      setMessages([]);
    }
  }, []);

  // Restore the conversation and document from the address bar.
  useEffect(() => {
    const { conversation, document, page } = readUrl();
    if (conversation) void openConversation(conversation);
    if (document) setViewer({ documentId: document, page, highlights: [], citationNumber: null, nonce: ++nonce.current });
  }, [openConversation]);

  // Keep the address bar in sync so a reload or a shared link reopens the same view.
  useEffect(() => {
    const params = new URLSearchParams();
    if (conversationId) params.set("c", conversationId);
    if (viewer) {
      params.set("doc", viewer.documentId);
      if (viewer.page > 1) params.set("p", String(viewer.page));
    }
    const query = params.toString();
    window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
  }, [conversationId, viewer]);

  const send = useCallback(
    async (question: string) => {
      if (busy) return;
      const answerId = localId();
      setMessages((previous) => [...previous, { id: localId(), role: "user", content: question }, pendingAnswer(answerId)]);
      setBusy(true);
      const abort = new AbortController();
      controller.current = abort;
      const update = (change: (message: ReturnType<typeof pendingAnswer>) => ReturnType<typeof pendingAnswer>) =>
        setMessages((previous) => previous.map((message) => (message.id === answerId && message.role === "assistant" ? change(message) : message)));
      try {
        await requestAnswerStream(
          { question, conversation_id: conversationId, scope },
          (event) => {
            if (event.type === "conversation") setConversationId(event.conversation_id);
            else update((message) => applyEvent(message, event));
          },
          { signal: abort.signal },
        );
      } catch {
        update((message) =>
          message.status === "streaming"
            ? { ...message, status: "error", stage: null, message: abort.signal.aborted ? "Réponse interrompue." : "La connexion au serveur a échoué. Vérifiez qu'il est démarré puis réessayez." }
            : message,
        );
      } finally {
        setBusy(false);
        controller.current = null;
        void refreshConversations();
      }
    },
    [busy, conversationId, scope, refreshConversations],
  );

  const newConversation = () => {
    controller.current?.abort();
    setConversationId(null);
    setMessages([]);
    setActiveCitation(null);
    setSidebarOpen(false);
  };

  const openCitation = (messageId: string, citation: Citation) => {
    setViewer(citationTarget(citation, ++nonce.current));
    setActiveCitation({ messageId, number: citation.number });
  };

  const openDocument = (document: DocumentSummary) => {
    setViewer({ documentId: document.id, page: 1, highlights: [], citationNumber: null, nonce: ++nonce.current });
    setScope({ document_id: document.id });
    setActiveCitation(null);
    setSidebarOpen(false);
  };

  const title = conversationId ? conversations.find((item) => item.id === conversationId)?.title ?? "Conversation" : "Nouvelle conversation";
  const scopeText = scopeLabel(scope, documents);

  return (
    <div className="flex h-screen overflow-hidden">
      <div className={clsx("fixed inset-y-0 left-0 z-40 transition-transform md:static md:translate-x-0", sidebarOpen ? "translate-x-0" : "-translate-x-full")}>
        <Sidebar
          courses={courses}
          documents={documents}
          libraryError={libraryError}
          conversations={conversations}
          activeConversationId={conversationId}
          scope={scope}
          onNewConversation={newConversation}
          onOpenConversation={(id) => void openConversation(id)}
          onRenameConversation={async (id, newTitle) => {
            await renameConversation(id, newTitle).catch(() => undefined);
            void refreshConversations();
          }}
          onDeleteConversation={async (id) => {
            await deleteConversation(id).catch(() => undefined);
            if (id === conversationId) newConversation();
            void refreshConversations();
          }}
          onSelectScope={(next) => {
            setScope(next);
            setSidebarOpen(false);
          }}
          onOpenDocument={openDocument}
        />
      </div>
      {sidebarOpen && <button type="button" className="fixed inset-0 z-30 bg-slate-900/40 md:hidden" onClick={() => setSidebarOpen(false)} aria-label="Fermer le menu" />}

      <div className="relative flex min-w-0 flex-1">
        <button type="button" onClick={() => setSidebarOpen(true)} className="absolute left-3 top-3 z-10 rounded-lg p-1.5 text-slate-600 hover:bg-slate-100 md:hidden" aria-label="Ouvrir le menu">
          <Menu className="h-5 w-5" aria-hidden />
        </button>
        <ChatPanel
          title={title}
          messages={messages}
          courses={courses}
          scope={scope}
          scopeText={scopeText}
          busy={busy}
          activeCitation={activeCitation}
          onSend={(question) => void send(question)}
          onStop={() => controller.current?.abort()}
          onClearScope={() => setScope({})}
          onOpenCitation={openCitation}
        />
        {viewer && (
          <div className="fixed inset-0 z-50 flex border-l border-slate-200 lg:static lg:z-auto lg:w-[48%] lg:min-w-[420px] lg:max-w-[900px]">
            <DocumentViewer
              target={viewer}
              scopedToDocument={scope.document_id === viewer.documentId}
              onPageChange={(page) => setViewer((current) => (current ? { ...current, page } : current))}
              onAskAboutDocument={(document) => setScope({ document_id: document.id })}
              onClose={() => {
                setViewer(null);
                setActiveCitation(null);
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
